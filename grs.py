#!/usr/bin/env python

import configparser
import pickle
import re
import webbrowser
from collections import defaultdict
from html import escape, parser
from gi import require_version
from gi.repository import GLib, Notify
from pathlib import Path
from xml.etree import ElementTree

require_version('Adw', '1')
require_version('Gdk', '4.0')
require_version('Gtk', '4.0')
require_version('Soup', '2.4')
from gi.repository import Adw, Gdk, Gtk, Soup  # noqa

CACHE_PATH = Path.home() / '.cache' / 'grs'
CONFIG = configparser.ConfigParser()
CONFIG.read(Path.home() / '.config' / 'grs')
SESSION = Soup.SessionAsync()
CACHE = (
    pickle.loads(CACHE_PATH.read_bytes()) if CACHE_PATH.is_file()
    else defaultdict(set))


class Article(object):
    def __init__(self, feed, tag):
        self.feed = feed
        title = tag.find(f'{self.feed.namespace}title').text
        self.title = title.strip() if title else ''

        link_tag = tag.find(f'{self.feed.namespace}link')
        self.link = (link_tag.attrib.get('href') or link_tag.text).strip()

        enclosure_tag = tag.find(f'{self.feed.namespace}enclosure')
        if enclosure_tag is not None and 'url' in enclosure_tag.attrib:
            if 'audio' in enclosure_tag.attrib.get('type', ''):
                self.link = enclosure_tag.attrib['url'].strip()

        self.description = ''
        for name in ('description', 'summary', 'content'):
            desc_tag = tag.find(self.feed.namespace + name)
            if desc_tag is not None and (desc_tag.text or len(desc_tag)):
                self.description = (desc_tag.text or ElementTree.tostring(
                    desc_tag[0], encoding='unicode'))
                break
        self.guid = None
        for name in ('id', 'guid', 'link'):
            guid_tag = tag.find(self.feed.namespace + name)
            if guid_tag is not None and guid_tag.text:
                self.guid = guid_tag.text
                break

    @property
    def read(self):
        return self.guid in CACHE[self.feed.url]


class Feed(Gtk.TreeView):
    def __init__(self, name):
        self.name = name
        self.url = CONFIG[name]['url']
        self.articles = []
        self.message = Soup.Message.new('GET', self.url)
        self.message.props.request_headers.append('User-Agent', 'GRS')

        super().__init__()
        self.set_model(Gtk.ListStore(object))
        self.set_headers_visible(False)
        self.set_hexpand(Gtk.Align.END)
        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        pane_cell.props.xpad = pane_cell.props.ypad = 5
        pane_cell.props.ellipsize = 3  # At the end
        pane_column.pack_start(pane_cell, True)
        pane_column.set_cell_data_func(pane_cell, self._render_cell)
        self.append_column(pane_column)

        self.connect('row-activated', self._activated)

    def update(self):
        cursor = self.get_cursor()[0]
        active = self.props.model[cursor[0]][0] if cursor else None
        self.set_cursor(len(self.props.model), None)  # Remove cursor
        self.props.model.clear()
        for article in self.articles:
            self.props.model.append((article,))
            if active and article.guid == active.guid:
                self.set_cursor(len(self.props.model) - 1, None)

    def _render_cell(self, column, cell, model, iter_, destroy):
        article = model[iter_][0]
        title = escape(re.sub(
            '\\s+', ' ', article.title.replace('\n', ' ').strip()))
        content = []
        html_parser = parser.HTMLParser()
        html_parser.handle_data = content.append
        html_parser.feed(article.description)
        content = escape(re.sub(
            '\\s+', ' ', ''.join(content)[:1000].replace('\n', ' ').strip()))
        cell.set_property('markup', '<big>%s</big>\n<small>%s</small>' % (
            ('%s' if article.read else '<b>%s</b>') % title, content))

    def _activated(self, treeview, path, view):
        webbrowser.open(treeview.props.model[path][0].link)


class FeedList(Gtk.StackSidebar):
    def set_attention(self, feed):
        attention = any(not article.read for article in feed.articles)
        page = self.props.stack.get_page(
            self.props.stack.get_child_by_name(feed.name))
        page.set_needs_attention(attention)


class Window(Gtk.ApplicationWindow):
    def __init__(self, application):
        super(Gtk.ApplicationWindow, self).__init__(application=application)
        self.set_title('GRS')
        self.set_icon_name('edit-find')
        self.last_article = None

        self.feed_list = FeedList()
        self.feed_list.set_stack(Gtk.Stack())
        self.feed_list.props.stack.set_transition_type(
            Gtk.StackTransitionType.CROSSFADE)
        hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        hbox.append(self.feed_list)
        hbox.append(self.feed_list.props.stack)
        self.set_child(hbox)

        gesture = Gtk.GestureClick.new()
        gesture.connect('pressed', self._feed_clicked)
        self.feed_list.add_controller(gesture)

        for section in CONFIG.sections():
            feed = Feed(section)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(feed)
            self.feed_list.props.stack.add_titled(scroll, section, section)
            feed.connect('cursor-changed', self._article_changed, feed)
            gesture = Gtk.GestureClick.new()
            gesture.set_button(Gdk.BUTTON_MIDDLE)
            gesture.connect('pressed', self._article_clicked, feed)
            feed.add_controller(gesture)

    def update(self, notify=True):
        for page in self.feed_list.props.stack.get_pages():
            feed = page.get_child().get_child()
            SESSION.queue_message(
                feed.message, self.update_after, feed, notify)

    def update_after(self, session, message, feed, notify):
        old_articles = [article.guid for article in feed.articles]
        xml = ElementTree.fromstring(
            message.props.response_body_data.get_data().strip())
        feed.namespace = (re.findall('\\{.*\\}', xml.tag) or ['']).pop()
        feed.articles = [
            Article(feed, tag) for tag_name in ('item', 'entry')
            for tag in xml.iter(feed.namespace + tag_name)]
        CACHE[feed.url] &= {article.guid for article in feed.articles}
        if notify:
            for article in feed.articles:
                if not article.read and article.guid not in old_articles:
                    Notify.Notification.new(
                        feed.name, article.title, 'edit-find').show()
        feed.update()
        self.feed_list.set_attention(feed)

    def _article_changed(self, treeview, feed):
        cursor = treeview.get_cursor()[0]
        if cursor:
            article = treeview.props.model[cursor[0]][0]
            if article == self.last_article:
                self.last_article = None
                return
            CACHE[article.feed.url].add(article.guid)
            CACHE_PATH.write_bytes(pickle.dumps(CACHE))
        self.feed_list.set_attention(feed)

    def _article_clicked(self, gesture, clicks, x, y, feed):
        treeview = gesture.get_widget()
        path = treeview.get_path_at_pos(x, y)
        if path:
            self.last_article = article = treeview.props.model[path[0]][0]
            if article.guid in CACHE[article.feed.url]:
                CACHE[article.feed.url].discard(article.guid)
            else:
                CACHE[article.feed.url].add(article.guid)
            treeview.queue_draw()
            CACHE_PATH.write_bytes(pickle.dumps(CACHE))
            self.feed_list.set_attention(feed)

    def _feed_clicked(self, gesture, clicks, x, y):
        feed_list = gesture.get_widget()
        if clicks == 2:
            visible_scroll = feed_list.props.stack.get_visible_child()
            visible_feed = visible_scroll.get_child()
            for article in visible_feed.articles:
                CACHE[visible_feed.url].add(article.guid)
            CACHE_PATH.write_bytes(pickle.dumps(CACHE))
            visible_feed.set_cursor([0])
            visible_feed.queue_draw()
            feed_list.set_attention(visible_feed)


class GRS(Adw.Application):
    def do_activate(self):
        Notify.init('GRS')
        self.window = Window(self)
        self.window.maximize()
        self.window.present()
        self.window.update(notify=False)
        GLib.timeout_add_seconds(180, lambda: self.window.update() or True)


GRS(application_id='fr.yabz.grs').run()
