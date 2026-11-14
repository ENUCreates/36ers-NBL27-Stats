#!/usr/bin/env python3
"""Make the Claude page from the website page: nbl27-website-updater/claude_page.html is index.html without its
document skeleton (the doctype, <html>, <head> with the charset/viewport metas and the small reset style, and the
closing </html>), because the artifact host wraps every page in that skeleton itself. Edit index.html, then run

    python3 nbl27-website-updater/build_claude_page.py [repo_root]

and publish both (see updater/claudepub.py for the Claude page)."""
import os, sys

PRE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
       '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
       'body{margin:0;font:14px/1.45 system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>\n')
SUF = '\n</html>\n'

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
s = open(os.path.join(root, "index.html"), encoding="utf-8").read()
if not (s.startswith(PRE) and s.endswith(SUF)):
    sys.exit("index.html doesn't start and end with the expected skeleton: not written")
out = os.path.join(root, "nbl27-website-updater", "claude_page.html")
open(out, "w", encoding="utf-8").write(s[len(PRE):-len(SUF)])
print("written", out, len(s) - len(PRE) - len(SUF), "chars")
