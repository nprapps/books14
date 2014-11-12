#!/usr/bin/env python

import app_config
import argparse
import json
import re
import static

from PIL import Image
from flask import Flask, make_response, render_template
from render_utils import make_context, smarty_filter, urlencode_filter

app = Flask(__name__)

app.add_template_filter(smarty_filter, name='smarty')
app.add_template_filter(urlencode_filter, name='urlencode')

@app.route('/')
def index():
    """
    The index page.
    """
    # Set up standard page context.
    context = make_context()

    # Read the books JSON into the page.
    with open('www/static-data/books.json', 'rb') as readfile:
        context['books_js'] = readfile.read()
        books = json.loads(context['books_js'])
        books_text_only = books[:]
        books_text_only = sorted(books, key=lambda k: k['title'])

    tag_stripper = re.compile(r'<.*?>')

    for book in books:
        if not book['text']:
            book['teaser'] = None
            continue

        img = Image.open('www/assets/cover/%s-thumb.jpg' % book['slug'])
        width, height = img.size

        # Poor man's packing algorithm. How much text will fit?
        chars = height / 25 * 10;

        text = tag_stripper.sub('', book['text'])

        if len(text) <= chars:
            book['teaser'] = text
            continue

        i = chars

        while text[i] != ' ':
            i -= 1

        book['teaser'] = '&#8220;' + text[:i] + ' ...&#8221;'

    context['books'] = books
    context['books_text_only'] = books_text_only

    return render_template('index.html', **context)

@app.route('/comments/')
def comments():
    """
    Full-page comments view.
    """
    return make_response(render_template('comments.html', **make_context()))

@app.route('/widget.html')
def widget():
    """
    Embeddable widget example page.
    """
    return make_response(render_template('widget.html', **make_context()))

@app.route('/test_widget.html')
def test_widget():
    """
    Example page displaying widget at different embed sizes.
    """
    return make_response(render_template('test_widget.html', **make_context()))

app.register_blueprint(static.static)

# Boilerplate
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port')
    args = parser.parse_args()
    server_port = 8000

    if args.port:
        server_port = int(args.port)

    app.run(host='0.0.0.0', port=server_port, debug=app_config.DEBUG)
