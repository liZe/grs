#!/usr/bin/env python

import configparser
import html.parser
import os
import re
import pickle
import subprocess
import sys
import urllib.request
from collections import defaultdict
from html import escape
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
    parser = html.parser.HTMLParser()
    parser.handle_data = content.append
    parser.feed(string)
    return re.sub('\s+', ' ', ''.join(content).replace('\n', ' ').strip())


class Article(object):
    def __init__(self, feed, tag):
        self.feed = feed
        self.tag = tag
        self.title = self.tag.find(self.feed.namespace + 'title').text
        link_tag = self.tag.find(self.feed.namespace + 'link')
        self.link = link_tag.attrib.get('href') or link_tag.text
        self.description = ''
        for name in ('description', 'summary', 'content'):
            tag = self.tag.find(self.feed.namespace + name)
            if tag is not None and (tag.text or len(tag)):
                self.description = (tag.text or ElementTree.tostring(
                    tag[0], encoding='unicode'))
                break
        self.guid = None
        for name in ('id', 'guid', 'link'):
            tag = self.tag.find(self.feed.namespace + name)
            if tag is not None and tag.text:
                self.guid = tag.text
                break

    def set_read(self):
        CACHE[self.feed.config['url']].add(self.guid)

    @property
    def read(self):
        return self.guid in CACHE[self.feed.config['url']]


class ArticleList(Gtk.TreeView):
    def __init__(self):
        super(ArticleList, self).__init__()
        self.set_model(Gtk.ListStore(str, object))
        self.set_headers_visible(False)

        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        pane_cell.props.ellipsize = 3  # At the end
        pane_column.pack_start(pane_cell, True)
        pane_column.set_cell_data_func(pane_cell, self._render_cell)
        self.append_column(pane_column)

    def update(self, feed):
        self.set_cursor(len(self.props.model), None)  # Remove cursor
        self.props.model.clear()
        for article in feed.articles:
            self.props.model.append((article.title, article))

    @staticmethod
    def _render_cell(column, cell, model, iter_, destroy):
        article = model[iter_][-1]
        cell.set_property('markup', '<big>%s</big>\n<small>%s</small>' % (
            ('%s' if article.read else '<b>%s</b>') % escape(article.title),
            textify(article.description)))


class Feed(object):
    def __init__(self, name):
        self.name = name
        self.config = CONFIG[name]
        self.xml = None
        self.namespace = ''
        self.update()

    def update(self):
        self.xml = ElementTree.fromstring(
            urllib.request.urlopen(self.config['url']).read())
        if '}' in self.xml.tag:
            self.namespace = self.xml.tag[:self.xml.tag.index('}') + 1]
        self.articles = [
            Article(self, tag) for tag_name in ('item', 'entry')
            for tag in self.xml.iter(self.namespace + tag_name)]
        CACHE[self.config['url']] &= {art.guid for art in self.articles}
        for article in self.articles:
            if not article.read:
                Notify.Notification.new(
                    'GRS', '%s - %s' % (self.name, article.title),
                    'edit-find').show()


class FeedList(Gtk.TreeView):
    def __init__(self):
        super(FeedList, self).__init__()
        self.set_model(Gtk.ListStore(str, object))
        self.set_headers_visible(False)

        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        pane_column.pack_start(pane_cell, True)
        pane_column.add_attribute(pane_cell, 'text', 0)
        self.append_column(pane_column)

        for section in CONFIG.sections():
            if section != '*':
                self.props.model.append((section, Feed(section)))


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

        self.feed_list.connect(
            'cursor-changed', lambda treeview: self.article_list.update(
                treeview.props.model[treeview.get_cursor()[0][0]][-1]))
        self.article_list.connect(
            'row-activated', lambda treeview, path, view: subprocess.Popen(
                CONFIG.get('*', 'browser', fallback='firefox').split() +
                [treeview.props.model[path][-1].link]))
        self.article_list.connect(
            'cursor-changed', lambda treeview:
            treeview.props.model[treeview.get_cursor()[0][0]][-1].set_read()
            if treeview.get_cursor()[0] else None)
        self.connect(
            'destroy', lambda window:
            pickle.dump(CACHE, open(CACHE_PATH, 'wb')) or sys.exit())


class GRS(Gtk.Application):
    def do_activate(self):
        Notify.init('GRS')
        self.window = Window(self)
        self.window.show_all()
        GLib.timeout_add_seconds(
            int(CONFIG.get('*', 'timer', fallback='300')),
            lambda: self.update() or True)

    def update(self):
        for feed_view in self.window.feed_list.props.model:
            feed = feed_view[-1]
            feed.update()
            if self.window.article_list.props.model[0][-1].feed == feed:
                self.window.article_list.update(feed)
        pickle.dump(CACHE, open(CACHE_PATH, 'wb'))


if __name__ == '__main__':
    GRS().run(sys.argv)
