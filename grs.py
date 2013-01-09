#!/usr/bin/env python

import configparser
import os
import pickle
import re
import sys
import urllib.request
import webbrowser
from collections import defaultdict
from html import escape, parser
from gi.repository import GLib, Gtk, Notify
from xml.etree import ElementTree


CONFIG_PATH = os.path.expanduser('~/.config/grs')
CACHE_PATH = os.path.expanduser('~/.cache/grs')
CONFIG = configparser.SafeConfigParser()
CONFIG.read(CONFIG_PATH)
CACHE = (
    pickle.load(open(CACHE_PATH, 'rb')) if os.path.exists(CACHE_PATH)
    else defaultdict(set))


def textify(string):
    content = []
    html_parser = parser.HTMLParser()
    html_parser.handle_data = content.append
    html_parser.feed(string)
    return re.sub('\s+', ' ', ''.join(content).replace('\n', ' ').strip())


class ListView(Gtk.TreeView):
    def __init__(self):
        super(ListView, self).__init__()
        self.set_model(Gtk.ListStore(object))
        self.set_headers_visible(False)

        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        pane_cell.props.ellipsize = 3  # At the end
        pane_column.pack_start(pane_cell, True)
        pane_column.set_cell_data_func(pane_cell, self._render_cell)
        self.append_column(pane_column)


class Article(object):
    def __init__(self, feed, tag):
        self.feed = feed
        self.title = tag.find(self.feed.namespace + 'title').text
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
        self.set_cursor(len(self.props.model), None)  # Remove cursor
        self.props.model.clear()
        for article in feed.articles:
            self.props.model.append((article,))

    def _render_cell(self, column, cell, model, iter_, destroy):
        article = model[iter_][0]
        cell.set_property('markup', '<big>%s</big>\n<small>%s</small>' % (
            ('%s' if article.read else '<b>%s</b>') % escape(article.title),
            textify(article.description)))


class Feed(object):
    def __init__(self, name):
        self.name = name
        self.url = CONFIG[name]['url']
        self.articles = []

    def update(self):
        old_articles = [article.guid for article in self.articles]
        xml = ElementTree.fromstring(urllib.request.urlopen(self.url).read())
        self.namespace = (re.findall('\{.*\}', xml.tag) or ['']).pop()
        self.articles = [
            Article(self, tag) for tag_name in ('item', 'entry')
            for tag in xml.iter(self.namespace + tag_name)]
        CACHE[self.url] &= {article.guid for article in self.articles}
        for article in self.articles:
            if not article.read and article.guid not in old_articles:
                Notify.Notification.new(
                    'GRS', '%s - %s' % (self.name, article.title),
                    'edit-find').show()


class FeedList(ListView):
    def __init__(self):
        super(FeedList, self).__init__()
        for section in CONFIG.sections():
            self.props.model.append((Feed(section),))

    @staticmethod
    def _render_cell(column, cell, model, iter_, destroy):
        feed = model[iter_][0]
        new_articles = len(
            [True for article in feed.articles if not article.read])
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
        self.panel.add1(scroll)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.article_list)
        self.panel.add2(scroll)
        self.add(self.panel)

        self.feed_list.connect('cursor-changed', self._feed_changed)
        self.article_list.connect('row-activated', self._article_activated)
        self.article_list.connect('cursor-changed', self._article_changed)

    def _feed_changed(self, treeview):
        self.article_list.update(
            treeview.props.model[treeview.get_cursor()[0][0]][0])

    def _article_activated(self, treeview, path, view):
        webbrowser.open(treeview.props.model[path][0].link)

    def _article_changed(self, treeview):
        if treeview.get_cursor()[0]:
            article = treeview.props.model[treeview.get_cursor()[0][0]][0]
            CACHE[article.feed.url].add(article.guid)
        self.feed_list.props.window.invalidate_rect(
            self.feed_list.get_visible_rect(), invalidate_children=True)


class GRS(Gtk.Application):
    def do_activate(self):
        Notify.init('GRS')
        self.window = Window(self)
        self.window.show_all()
        self.window.connect('destroy', lambda window: self.quit())
        self.update()
        GLib.timeout_add_seconds(180, lambda: self.update() or True)

    def update(self):
        model = self.window.feed_list.props.model
        for feed_view in model:
            feed = feed_view[0]
            feed.update()
            cursor = self.window.feed_list.get_cursor()[0]
            if cursor and model[cursor[0]][0] == feed:
                self.window.article_list.update(feed)
            while Gtk.events_pending():
                Gtk.main_iteration()
        pickle.dump(CACHE, open(CACHE_PATH, 'wb'))

    def quit(self):
        pickle.dump(CACHE, open(CACHE_PATH, 'wb'))
        sys.exit()


if __name__ == '__main__':
    GRS().run(sys.argv)
