#!/usr/bin/env python

from gi.repository import Gtk

import sys
import os
import configparser
import urllib.request
import subprocess
from xml.etree import ElementTree


CONFIG = configparser.SafeConfigParser()
CONFIG.read(os.path.expanduser('~/.config/grs'))


class Article(object):
    def __init__(self, feed, tag):
        self.feed = feed
        self.tag = tag
        self.title = self.tag.find(self.feed.namespace + 'title').text
        link_tag = self.tag.find(self.feed.namespace + 'link')
        self.link = link_tag.attrib.get('href') or link_tag.text


class ArticleList(Gtk.TreeView):
    def __init__(self):
        super(ArticleList, self).__init__()
        self.set_model(Gtk.ListStore(str, object))
        self.set_headers_visible(False)

        pane_column = Gtk.TreeViewColumn()
        pane_cell = Gtk.CellRendererText()
        pane_column.pack_start(pane_cell, True)
        pane_column.add_attribute(pane_cell, 'text', 0)
        self.append_column(pane_column)

    def update(self, feed):
        self.props.model.clear()
        for article in feed.articles:
            self.props.model.append((article.title, article))


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
        self.articles = (
            Article(self, tag) for tag_name in ('item', 'entry')
            for tag in self.xml.iter(self.namespace + tag_name))


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

        self.update()

    def update(self):
        self.props.model.clear()
        for section in CONFIG.sections():
            self.props.model.append((section, Feed(section)))


class Window(Gtk.ApplicationWindow):
    def __init__(self, application):
        super(Gtk.ApplicationWindow, self).__init__(application=application)
        self.set_title('Gnome RSS Stalker')
        self.set_hide_titlebar_when_maximized(True)
        self.set_icon_name('text-html')

        self.panel = Gtk.HPaned()
        self.feed_list = FeedList()
        self.panel.add1(self.feed_list)
        self.article_list = ArticleList()
        self.panel.add2(self.article_list)
        self.add(self.panel)

        self.feed_list.connect(
            'row-activated', lambda treeview, path, view:
            self.article_list.update(treeview.get_model()[path][1]))
        self.article_list.connect(
            'row-activated', lambda treeview, path, view:
            subprocess.call(['epiphany', treeview.get_model()[path][1].link]))
        self.connect('destroy', lambda window: sys.exit())


class GRS(Gtk.Application):
    def do_activate(self):
        Window(self).show_all()

if __name__ == '__main__':
    GRS().run(sys.argv)
