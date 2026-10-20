"""Public, credential-free GUI input fixtures; installed only in disposable lab."""
import os
from pathlib import Path
import sys
kind=sys.argv[1]
os.umask(0o007)
dest=Path('/home/wdguest/.gui/ime-'+kind+'.txt')
if kind=='gtk':
 import gi
 gi.require_version('Gtk','3.0')
 from gi.repository import Gtk
 window=Gtk.Window(title='WD IME GTK');window.set_default_size(600,180)
 entry=Gtk.Entry();entry.set_placeholder_text('Pinyin input test');entry.connect('changed',lambda e:dest.write_text(e.get_text()))
 window.add(entry);window.connect('destroy',Gtk.main_quit);window.show_all();entry.grab_focus();Gtk.main()
elif kind=='qt':
 from PyQt5.QtWidgets import QApplication,QWidget,QLineEdit,QVBoxLayout
 app=QApplication([]);window=QWidget();window.setWindowTitle('WD IME Qt');window.resize(600,180)
 layout=QVBoxLayout(window);entry=QLineEdit();entry.textChanged.connect(lambda text:dest.write_text(text));layout.addWidget(entry);window.show();entry.setFocus();app.exec_()
else:raise ValueError('Fixed toolkit')
