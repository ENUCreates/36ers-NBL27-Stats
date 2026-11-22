#!/usr/bin/env python3
"""Make the Claude page from the website page. Edit index.html, then run

    python3 nbl27-website-updater/build_claude_page.py [repo_root]

and publish both (see updater/claudepub.py for the Claude page). It writes two files:
  nbl27-website-updater/claude_page.html   index.html without its document skeleton (the doctype, <html>, <head> with
                                           the charset/viewport metas and the small reset style, and the closing
                                           </html>): the page content, which claudepub.py tracks
  nbl27-website-updater/claude_index.html  that content inside the artifact host's own skeleton, exactly as the old
                                           Artifact tool published it: the file to publish as the artifact's
                                           index.html (publish_files stores the file it is given as it is)"""
import os, sys

PRE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
       '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
       'body{margin:0;font:14px/1.45 system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>\n')
SUF = '\n</html>\n'
# the artifact host's skeleton (the shape the Artifact tool published, and the one a page republishing itself must use)
HOST = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,'
        'viewport-fit=cover"><style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);'
        'padding-bottom:env(safe-area-inset-bottom,0px)}html{scroll-padding-top:env(safe-area-inset-top,0px)}body{margin:0;'
        'padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;background:#fff;color:#000}img{max-width:100%}'
        '[hidden]:not([hidden=until-found i]){display:none!important}</style></head><body>\n')
HOST_END = '\n</body></html>'

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
s = open(os.path.join(root, "index.html"), encoding="utf-8").read()
if not (s.startswith(PRE) and s.endswith(SUF)):
    sys.exit("index.html doesn't start and end with the expected skeleton: not written")
body = s[len(PRE):-len(SUF)]
out = os.path.join(root, "nbl27-website-updater", "claude_page.html")
open(out, "w", encoding="utf-8").write(body)
full = os.path.join(root, "nbl27-website-updater", "claude_index.html")
open(full, "w", encoding="utf-8").write(HOST + body + HOST_END)
print("written", out, len(body), "chars, and", full)
