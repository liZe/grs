#!/usr/bin/env python

import configparser
import os
import pickle
import re
import sys
import webbrowser
from collections import defaultdict
from html import escape, parser
from gi.repository import GLib, Gtk, Gdk, Notify, Soup
from xml.etree import ElementTree


CONFIG_PATH = os.path.expanduser('~/.config/grs')
CACHE_PATH = os.path.expanduser('~/.cache/grs')
CONFIG = configparser.SafeConfigParser()
CONFIG.read(CONFIG_PATH)
SESSION = Soup.SessionAsync()
CACHE = (
    pickle.load(open(CACHE_PATH, 'rb')) if os.path.exists(CACHE_PATH)
    else defaultdict(set))


class Article(object):
    def __init__(self, feed, tag):
        self.feed = feed
        title = tag.find(self.feed.namespace + 'title').text
        self.title = title.strip() if title else ''
        link_tag = tag.find(self.feed.namespace + 'link')
        self.link = link_tag.attrib.get('href') or link_tag.text
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

        super().__init__()
        self.set_model(Gtk.ListStore(object))
        self.set_headers_visible(False)
        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
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
        content = []
        html_parser = parser.HTMLParser()
        html_parser.handle_data = content.append
        html_parser.feed(article.description)
        content = ''.join(content)[:1000]
        cell.set_property('markup', '<big>%s</big>\n<small>%s</small>' % (
            ('%s' if article.read else '<b>%s</b>') % escape(article.title),
            re.sub('\s+', ' ', escape(content).replace('\n', ' ').strip())))

    def _activated(self, treeview, path, view):
        webbrowser.open(treeview.props.model[path][0].link)


class FeedList(Gtk.StackSidebar):
    def set_attention(self, feed):
        self.props.stack.child_set_property(
            self.props.stack.get_child_by_name(feed.name), 'needs-attention',
            any(not article.read for article in feed.articles))


class Window(Gtk.ApplicationWindow):
    def __init__(self, application):
        super(Gtk.ApplicationWindow, self).__init__(application=application)
        self.set_title('Gnome RSS Stalker')
        self.set_hide_titlebar_when_maximized(True)
        self.set_icon_name('edit-find')

        self.feed_list = FeedList()
        self.feed_list.set_stack(Gtk.Stack())
        self.feed_list.props.stack.set_transition_type(
            Gtk.StackTransitionType.CROSSFADE)
        hbox = Gtk.HBox()
        hbox.pack_start(self.feed_list, False, False, 0)
        hbox.pack_start(self.feed_list.props.stack, True, True, 0)
        self.add(hbox)

        self.feed_list.connect('button-press-event', self._feed_clicked)

        for section in CONFIG.sections():
            feed = Feed(section)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.add(feed)
            self.feed_list.props.stack.add_titled(scroll, section, section)
            feed.connect('cursor-changed', self._article_changed, feed)
            feed.connect('button-press-event', self._article_clicked, feed)

    def update(self, notify=True):
        for scroll in self.feed_list.props.stack.get_children():
            feed = scroll.get_children()[0]
            SESSION.queue_message(
                feed.message, self.update_after, feed, notify)

    def update_after(self, session, message, feed, notify):
        old_articles = [article.guid for article in feed.articles]
        xml = ElementTree.fromstring(
            message.props.response_body_data.get_data().strip())
        feed.namespace = (re.findall('\{.*\}', xml.tag) or ['']).pop()
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
        if treeview.get_cursor()[0]:
            article = treeview.props.model[treeview.get_cursor()[0][0]][0]
            CACHE[article.feed.url].add(article.guid)
            pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
        self.feed_list.set_attention(feed)

    def _article_clicked(self, treeview, event, feed):
        if event.button == Gdk.BUTTON_MIDDLE:
            path = treeview.get_path_at_pos(event.x, event.y)
            if path:
                article = treeview.props.model[path[0]][0]
                if article.guid in CACHE[article.feed.url]:
                    CACHE[article.feed.url].discard(article.guid)
                else:
                    CACHE[article.feed.url].add(article.guid)
                treeview.queue_draw()
                pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
                self.feed_list.set_attention(feed)
                return True

    def _feed_clicked(self, feed_list, event):
        if event.type == getattr(Gdk.EventType, '2BUTTON_PRESS') and (
                event.button == Gdk.BUTTON_PRIMARY):
            visible_scroll = feed_list.props.stack.get_visible_child()
            visible_feed = visible_scroll.get_children()[0]
            for article in visible_feed.articles:
                CACHE[visible_feed.url].add(article.guid)
            pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
            visible_feed.set_cursor([0])
            visible_feed.queue_draw()
            feed_list.set_attention(visible_feed)
            return True


class GRS(Gtk.Application):
    def do_activate(self):
        Notify.init('GRS')
        self.window = Window(self)
        self.window.maximize()
        self.window.connect('destroy', lambda window: sys.exit())
        self.window.show_all()
        self.window.update(notify=False)
        GLib.timeout_add_seconds(180, lambda: self.window.update() or True)


if __name__ == '__main__':
    GRS().run(sys.argv)
