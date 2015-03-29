#!/usr/bin/env python

import configparser
import os
import pickle
import re
import sys
import webbrowser
from collections import defaultdict
from html import escape, parser
from gi.repository import GLib, Gtk, Notify, Soup
from xml.etree import ElementTree


CONFIG_PATH = os.path.expanduser('~/.config/grs')
CACHE_PATH = os.path.expanduser('~/.cache/grs')
CONFIG = configparser.SafeConfigParser()
CONFIG.read(CONFIG_PATH)
SESSION = Soup.SessionAsync()
CACHE = (
    pickle.load(open(CACHE_PATH, 'rb')) if os.path.exists(CACHE_PATH)
    else defaultdict(set))


class ListView(Gtk.TreeView):
    def __init__(self, ellipsize=True):
        super(ListView, self).__init__()
        self.set_model(Gtk.ListStore(object))
        self.set_headers_visible(False)
        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        if ellipsize:
            pane_cell.props.ellipsize = 3  # At the end
        pane_column.pack_start(pane_cell, True)
        pane_column.set_cell_data_func(pane_cell, self._render_cell)
        self.append_column(pane_column)

    def redraw(self):
        self.get_bin_window().invalidate_rect(
            self.get_visible_rect(), invalidate_children=True)


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


class ArticleList(ListView):
    def update(self, feed):
        cursor = self.get_cursor()[0]
        active = self.props.model[cursor[0]][0] if cursor else None
        self.set_cursor(len(self.props.model), None)  # Remove cursor
        self.props.model.clear()
        for article in feed.articles:
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


class Feed(object):
    def __init__(self, name):
        self.name = name
        self.url = CONFIG[name]['url']
        self.articles = []
        self.message = Soup.Message.new('GET', self.url)


class FeedList(ListView):
    def __init__(self):
        super(FeedList, self).__init__(ellipsize=False)
        for section in CONFIG.sections():
            self.props.model.append((Feed(section),))

    @staticmethod
    def _render_cell(column, cell, model, iter_, destroy):
        feed = model[iter_][0]
        new_articles = sum(1 for article in feed.articles if not article.read)
        cell.set_property('markup', '<b>%s (%i)</b>' % (
            feed.name, new_articles) if new_articles else feed.name)


class Window(Gtk.ApplicationWindow):
    def __init__(self, application):
        super(Gtk.ApplicationWindow, self).__init__(application=application)
        self.set_title('Gnome RSS Stalker')
        self.set_hide_titlebar_when_maximized(True)
        self.set_icon_name('edit-find')

        self.panel = Gtk.HPaned()
        self.feed_list = FeedList()
        self.article_list = ArticleList()

        scroll = Gtk.ScrolledWindow()
        scroll.add(self.feed_list)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.panel.add1(scroll)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.article_list)
        self.panel.add2(scroll)
        self.add(self.panel)

        self.feed_list.connect('cursor-changed', self._feed_changed)
        self.feed_list.connect('button-press-event', self._feed_clicked)
        self.article_list.connect('row-activated', self._article_activated)
        self.article_list.connect('cursor-changed', self._article_changed)
        self.article_list.connect('button-press-event', self._article_clicked)

    def update(self):
        for feed_view in self.feed_list.props.model:
            SESSION.queue_message(
                feed_view[0].message, self.update_after, feed_view[0])

    def update_after(self, session, message, feed):
        old_articles = [article.guid for article in feed.articles]
        xml = ElementTree.fromstring(
            message.props.response_body_data.get_data().strip())
        feed.namespace = (re.findall('\{.*\}', xml.tag) or ['']).pop()
        feed.articles = [
            Article(feed, tag) for tag_name in ('item', 'entry')
            for tag in xml.iter(feed.namespace + tag_name)]
        CACHE[feed.url] &= {article.guid for article in feed.articles}
        for article in feed.articles:
            if not article.read and article.guid not in old_articles:
                Notify.Notification.new(
                    feed.name, article.title, 'edit-find').show()
        cursor = self.feed_list.get_cursor()[0]
        if cursor and self.feed_list.props.model[cursor[0]][0] == feed:
            self.article_list.update(feed)

    def _feed_changed(self, treeview):
        self.article_list.update(
            treeview.props.model[treeview.get_cursor()[0][0]][0])

    def _article_activated(self, treeview, path, view):
        webbrowser.open(treeview.props.model[path][0].link)

    def _article_changed(self, treeview):
        if treeview.get_cursor()[0]:
            article = treeview.props.model[treeview.get_cursor()[0][0]][0]
            CACHE[article.feed.url].add(article.guid)
            pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
        self.feed_list.redraw()

    def _article_clicked(self, treeview, event):
        if event.button == 2:  # Middle-click
            path = treeview.get_path_at_pos(event.x, event.y)
            if path:
                article = treeview.props.model[path[0]][0]
                if article.guid in CACHE[article.feed.url]:
                    CACHE[article.feed.url].discard(article.guid)
                else:
                    CACHE[article.feed.url].add(article.guid)
                treeview.redraw()
                self.feed_list.redraw()
                pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
                return True

    def _feed_clicked(self, treeview, event):
        if event.button == 2:  # Middle-click
            path = treeview.get_path_at_pos(event.x, event.y)
            if path:
                feed = treeview.props.model[path[0]][0]
                for article in feed.articles:
                    CACHE[feed.url].add(article.guid)
                pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
                treeview.redraw()
                self.article_list.redraw()
                return True


class GRS(Gtk.Application):
    def do_activate(self):
        Notify.init('GRS')
        self.window = Window(self)
        self.window.maximize()
        self.window.connect('destroy', lambda window: sys.exit())
        self.window.show_all()
        self.window.update()
        GLib.timeout_add_seconds(180, lambda: self.window.update() or True)


if __name__ == '__main__':
    GRS().run(sys.argv)
