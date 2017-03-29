# -*- coding: utf-8 -*-

import webbrowser

import tkinter as tk
from tkinter import ttk, messagebox

from thonny import misc_utils, tktextext
from thonny.globals import get_workbench, get_runner
import subprocess
import collections
import threading
from urllib.request import urlopen
import urllib.error
from concurrent.futures.thread import ThreadPoolExecutor
import os
import json
from distutils.version import LooseVersion
import logging

_NEW_PACKAGE_CAPTION = "<INSTALL>"

class PipDialog(tk.Toplevel):
    def __init__(self, master):
        self._state = None # possible values: "listing", "fetching", "idle"
        self._process = None
        self._installed_versions = {}
        self.current_package_data = None
        
        tk.Toplevel.__init__(self, master)
        
        
        width = 700
        height = 350
        self.geometry("%dx%d+%d+%d" % (width, height,
            master.winfo_rootx() + master.winfo_width() // 2 - width//2,
            master.winfo_rooty() + master.winfo_height() // 2 - height//2))

        main_frame = tk.Frame(self)
        main_frame.grid(sticky=tk.NSEW, ipadx=15, ipady=15)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.title("Manage packages for C:\\Users\\Aivar\\.thonny\\BundledPython36\\python.exe")
        if misc_utils.running_on_mac_os():
            self.configure(background="systemSheetBackground")
        #self.resizable(height=tk.FALSE, width=tk.FALSE)
        self.transient(master)
        self.grab_set() # to make it active
        self.grab_release() # to allow eg. copy something from the editor 
        
        self._create_widgets(main_frame)
        
        self.search_box.focus_set()
        
        self.bind('<Escape>', self._on_close, True) 
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        #self.listbox.selection_set(0)
        self._show_instructions()
        self._start_update_list()
    
    def _set_state(self, state):
        self._state = state
        widgets = [self.listbox, 
                           # self.search_box, # looks funny when disabled 
                           self.search_button,
                           self.install_button, self.advanced_button, self.uninstall_button]
        if state == "idle":
            self.config(cursor="")
            for widget in widgets:
                widget["state"] = tk.NORMAL
        else:
            self.config(cursor="wait")
            for widget in widgets:
                widget["state"] = tk.DISABLED
    
    def _get_state(self):
        return self._state
    
    def _start_update_list(self, name_to_show=None):
        assert self._get_state() in [None, "idle"]
        self._set_state("listing")
        self._process = _create_pip_process(["list", "--format", "json"])
        
        def poll_completion():
            if self._process == None:
                return
            else:
                returncode = self._process.poll()
                if returncode is None:
                    # not done yet
                    self.after(200, poll_completion)
                else:
                    self._set_state("idle")
                    if returncode == 0:
                        raw_data = self._process.stdout.read()
                        self._update_list(json.loads(raw_data))
                        if name_to_show is not None:
                            self._start_show_package_info(name_to_show)
                    else:   
                        messagebox.showerror("pip list error", self._process.stdout.read())
                    
                    self._process = None
        
        poll_completion()
                    
        
    def _update_list(self, data):
        self.listbox.delete(1, "end")
        self._installed_versions = {entry["name"] : entry["version"] for entry in data}
        for name in sorted(self._installed_versions.keys(), key=lambda x: x.lower()):
            self.listbox.insert("end", name)
        
        
    
    def _on_listbox_select(self, event):
        selection = self.listbox.curselection()
        if len(selection) == 1:
            if selection[0] == 0: # special first item
                self._show_instructions()
            else:
                self._start_show_package_info(self.listbox.get(selection[0]))
    
    def _on_search(self, event=None):
        self._start_show_package_info(self.search_box.get())
    
    def _show_instructions(self):
        self.current_package_data = None
        self.name_label.grid_remove()
        self.command_frame.grid_remove()
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", "Installing a package\n", ("caption",))
        self.info_text.insert("end", "Start by entering the name of the package in the search box above and pressing ENTER.\n\n")
        self.info_text.insert("end", "Upgrading or uninstalling a package\n", ("caption",))
        self.info_text.insert("end", 'Start by selecting the package from the left.\n\n')
    
    def _start_show_package_info(self, name):
        self.current_package_data = None
        self.info_text.delete("1.0", "end")
        self.name_label["text"] = ""
        self.name_label.grid()
        self.command_frame.grid()
        
        if name in self._installed_versions:
            self.name_label["text"] = name
            self.info_text.insert("end", "Installed version: ", ('caption',))
            self.info_text.insert("end", self._installed_versions[name] + "\n")
        
        
        # Fetch info from PyPI  
        self._set_state("fetching")
        # Follwing url fetches info about latest version.
        # This is OK even when we're looking an installed older version
        # because new version may have more relevant and complete info.
        url = "https://pypi.python.org/pypi/{}/json".format(name)
        url_future = _fetch_url_future(url)
            
        def poll_fetch_complete():
            if url_future.done():
                self._set_state("idle")
                try:
                    _, bin_data = url_future.result()
                    raw_data = bin_data.decode("UTF-8")
                    # TODO: check for 404
                    self._show_package_info(json.loads(raw_data))
                except urllib.error.HTTPError as e:
                    self._show_package_info(None, e.code)
                        
            else:
                self.after(200, poll_fetch_complete)
        
        poll_fetch_complete()

    def _show_package_info(self, data, error_code=None):
        self.current_package_data = data
        def write(s, tag=None):
            if tag is None:
                tags = ()
            else:
                tags = (tag,)
            self.info_text.insert("end", s, tags)
        
        def write_att(caption, value, value_tag=None):
            write(caption + ": ", "caption")
            write(value, value_tag)
            write("\n")
            
        if error_code is not None:
            if error_code == 404:
                write("Could not find the package. Please check spelling!")
            else:
                write("Could not find the package info. Error code: " + str(error_code))
            return
        
        info = data["info"]
        self.name_label["text"] = info["name"] # search name could have been a bit different
        latest_stable_version = _get_latest_stable_version(data["releases"].keys())
        if latest_stable_version is not None:
            write_att("Latest stable version", latest_stable_version)
        write_att("Summary", info["summary"])
        write_att("Author", info["author"])
        write_att("Homepage", info["home_page"], "url")
        if info.get("bugtrack_url", None):
            write_att("Bugtracker", info["bugtrack_url"], "url")
        if info.get("docs_url", None):
            write_att("Documentation", info["docs_url"], "url")
        if info.get("package_url", None):
            write_att("PyPI page", info["package_url"], "url")
        
        self.listbox.select_clear(0, "end")
        if self._get_installed_version(info["name"]) is not None:
            self.install_button["text"] = "Upgrade"
            self.uninstall_button.grid(row=0, column=2)
            items = list(map(str.lower, self.listbox.get(0, "end")))
            self.listbox.select_set(items.index(info["name"].lower()))
            if self._get_installed_version(info["name"]) == latest_stable_version:
                self.install_button["state"] = "disabled"
            else: 
                self.install_button["state"] = "normal"
        else:
            self.install_button["text"] = "Install"
            self.uninstall_button.grid_forget()
            self.listbox.select_set(0)
            
    
    def _create_widgets(self, parent):
        
        header_frame = ttk.Frame(parent)
        header_frame.grid(row=0, column=0, sticky="nsew", padx=15, pady=(15,0))
        header_frame.columnconfigure(0, weight=1)
        header_frame.rowconfigure(0, weight=1)
        
        name_font = tk.font.nametofont("TkDefaultFont").copy()
        name_font.configure(size=16)
        self.search_box = ttk.Entry(header_frame)
        self.search_box.grid(row=0, column=0, sticky="nsew")
        self.search_box.bind("<Return>", self._on_search, False)
        
        self.search_button = ttk.Button(header_frame, text="Search", command=self._on_search)
        self.search_button.grid(row=0, column=1, sticky="nse", padx=(10,0))
        
        
        main_pw = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        main_pw.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        
        listframe = ttk.Frame(main_pw)
        
        self.listbox = tk.Listbox(listframe, activestyle="dotbox", width=25)
        self.listbox.insert("end", _NEW_PACKAGE_CAPTION)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select, True)
        self.listbox.grid(row=0, column=0, sticky="nsew") 
        listframe.rowconfigure(0, weight=1)
        listframe.columnconfigure(0, weight=1)
        
        info_frame = tk.Frame(main_pw)
        info_frame.columnconfigure(0, weight=1)
        info_frame.rowconfigure(1, weight=1)
        
        main_pw.add(listframe, weight=1)
        main_pw.add(info_frame, weight=3)
        
        self.name_label = ttk.Label(info_frame, text="", font=name_font)
        self.name_label.grid(row=0, column=0, sticky="w", padx=5)
        

        
        info_text_frame = tktextext.TextFrame(info_frame,
                                              horizontal_scrollbar=False)
        info_text_frame.configure(borderwidth=1)
        info_text_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(0,20))
        self.info_text = info_text_frame.text
        self.info_text.tag_configure("url", foreground="#3A66DD", underline=True)
        self.info_text.tag_bind("url", "<ButtonRelease-1>", self._handle_url_click)
        self.info_text.tag_bind("url", "<Enter>", lambda e: self.info_text.config(cursor="hand2"))
        self.info_text.tag_bind("url", "<Leave>", lambda e: self.info_text.config(cursor=""))
        
        self.info_text.configure(background="SystemButtonFace",
                                 font=tk.font.nametofont("TkDefaultFont"),
                                 wrap="word")
        bold_font = tk.font.nametofont("TkDefaultFont").copy()
        bold_font.configure(weight="bold")
        self.info_text.tag_configure("caption", font=bold_font)
        
        
        self.command_frame = ttk.Frame(info_frame)
        self.command_frame.grid(row=2, column=0, sticky="w")
        
        self.install_button = ttk.Button(self.command_frame, text=" Upgrade ",
                                         command=lambda: self._perform_action("install"))
        self.install_button.grid(row=0, column=0, sticky="w")
        
        self.advanced_button = ttk.Button(self.command_frame, text=" Advanced ... ",
                                          command=lambda: self._perform_action("advanced"))
        #self.advanced_button.grid(row=0, column=1, sticky="w")
        
        self.uninstall_button = ttk.Button(self.command_frame, text="Uninstall",
                                           command=lambda: self._perform_action("uninstall"))
        self.uninstall_button.grid(row=0, column=2, sticky="w")
    
        self.close_button = ttk.Button(info_frame, text="Close", command=self._on_close)
        self.close_button.grid(row=2, column=3, sticky="e")
        
        #self._load_package_info("thonny", "2.0.7")
    
    def _perform_action(self, action):
        assert self._get_state() == "idle"
        assert self.current_package_data is not None
        data = self.current_package_data
        name = self.current_package_data["info"]["name"]
        
        if action == "install":
            args = ["install", name]
        elif action == "uninstall":
            args = ["uninstall", "-y", name]
        elif action == "advanced":
            args = self._ask_advanced_args(name, data)
            if args is None: # Cancel
                return
        else:
            raise RuntimeError("Unknown action")
        
        proc = _create_pip_process(args)
        # following call blocks
        _visualize_and_wait(proc)
        self._start_update_list(name)
        
    
    def _ask_advanced_args(self, name, data):
        # TODO: make the dialog
        #return ["install",  "--upgrade", name]
        return None
    
    def _handle_url_click(self, event):
        # http://stackoverflow.com/a/33957256/261181
        try:
            index = self.info_text.index("@%s,%s" % (event.x, event.y))
            tag_indices = list(self.info_text.tag_ranges('url'))
            for start, end in zip(tag_indices[0::2], tag_indices[1::2]):
                # check if the tag matches the mouse click index
                if self.info_text.compare(start, '<=', index) and self.info_text.compare(index, '<', end):
                    url = self.info_text.get(start, end)
                    webbrowser.open(url)
        except:
            logging.exception("URL clicking")
    
    def _on_close(self, event=None):
        self.destroy()
        
    def _get_installed_version(self, name):
        # looks like pip list is not precise about names
        for list_name in self._installed_versions:
            if name.lower() == list_name.lower():
                return self._installed_versions[list_name]
        
        return None


def _fetch_url_future(url, timeout=10):
    def load_url():
        with urlopen(url, timeout=timeout) as conn:
            return (conn, conn.read())
            
    executor = ThreadPoolExecutor(max_workers=1)
    return executor.submit(load_url)


def _create_pip_process(args):
    encoding = "UTF-8"
    env = {}
    for name in os.environ:
        if ("python" not in name.lower() and name not in ["TK_LIBRARY", "TCL_LIBRARY"]): # skip python vars
            env[name] = os.environ[name]
            
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONUNBUFFERED"] = "1"
                
    interpreter = get_runner().get_interpreter_command()
    cmd = [interpreter, "-m", "pip"] + args
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, universal_newlines=True, encoding=encoding)

def _execute_system_command_and_wait(cmd):
    encoding = "UTF-8"
    env = {"PYTHONIOENCODING" : encoding,
           "PYTHONUNBUFFERED" : "1"}
    
    try:
        output = subprocess.check_output(cmd,
                            stderr=subprocess.STDOUT, env=env,
                            universal_newlines=True, encoding=encoding)
        return (0, output)
    except subprocess.CalledProcessError as e:
        return (e.returncode, e.output)
    

def _direct_system_command_to_queue(cmd, event_queue):
    encoding = "UTF-8"
    env = {"PYTHONIOENCODING" : encoding,
           "PYTHONUNBUFFERED" : "1"}
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            universal_newlines=True, encoding=encoding)
    
    proc.events = collections.deque()
    
    def listen_stream(stream_name):
        stream = getattr(proc.stdout, stream_name)
        while True:
            data = stream.readline()
            if data == '':
                break
            else:
                event_queue.append((stream_name, data))
        
        returncode = proc.wait()
        event_queue.append(("returncode", returncode))
    
    threading.Thread(target=listen_stream, args=["stdout"]).start()
    threading.Thread(target=listen_stream, args=["stderr"]).start()
    
    return proc 
    
    
def _get_latest_stable_version(version_strings):
    versions = []
    for s in version_strings:
        if s.replace(".", "").isnumeric(): # Assuming stable versions have only dots and numbers
            versions.append(LooseVersion(s))
    
    if len(versions) == 0:
        return None
        #versions = [LooseVersion(v) for v in version_strings]
        
    return str(sorted(versions)[-1])


def _visualize_and_wait(proc):
    # TODO: wait in dialog
    return proc.wait()


def load_plugin():
    def open_pip_gui(*args):
        pg = PipDialog(get_workbench())
        pg.wait_window()

        
    get_workbench().add_command("pipgui", "tools", "Manage packages...", open_pip_gui,
                                group=80)


    