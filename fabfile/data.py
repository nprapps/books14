#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Commands that update or process the application data.
"""
import app_config
import copytext
import csv
import json
import os
import re
import requests
import xlrd

from PIL import Image
from bs4 import BeautifulSoup
from datetime import datetime
from fabric.api import task
from facebook import GraphAPI
from twitter import Twitter, OAuth

TAGS_TO_SLUGS = {}
SLUGS_TO_TAGS = {}

@task(default=True)
def update():
    """
    Load books and covers
    """
    load_books()
    load_images()
    #update_featured_social()


@task
def update_featured_social():
    """
    Update featured tweets
    """
    COPY = copytext.Copy(app_config.COPY_PATH)
    secrets = app_config.get_secrets()

    # Twitter
    print 'Fetching tweets...'

    twitter_api = Twitter(
        auth=OAuth(
            secrets['TWITTER_API_OAUTH_TOKEN'],
            secrets['TWITTER_API_OAUTH_SECRET'],
            secrets['TWITTER_API_CONSUMER_KEY'],
            secrets['TWITTER_API_CONSUMER_SECRET']
        )
    )

    tweets = []

    for i in range(1, 4):
        tweet_url = COPY['share']['featured_tweet%i' % i]

        if isinstance(tweet_url, copytext.Error) or unicode(tweet_url).strip() == '':
            continue

        tweet_id = unicode(tweet_url).split('/')[-1]

        tweet = twitter_api.statuses.show(id=tweet_id)

        creation_date = datetime.strptime(tweet['created_at'],'%a %b %d %H:%M:%S +0000 %Y')
        creation_date = '%s %i' % (creation_date.strftime('%b'), creation_date.day)

        tweet_url = 'http://twitter.com/%s/status/%s' % (tweet['user']['screen_name'], tweet['id'])

        photo = None
        html = tweet['text']
        subs = {}

        for media in tweet['entities'].get('media', []):
            original = tweet['text'][media['indices'][0]:media['indices'][1]]
            replacement = '<a href="%s" target="_blank" onclick="_gaq.push([\'_trackEvent\', \'%s\', \'featured-tweet-action\', \'link\', 0, \'%s\']);">%s</a>' % (media['url'], app_config.PROJECT_SLUG, tweet_url, media['display_url'])

            subs[original] = replacement

            if media['type'] == 'photo' and not photo:
                photo = {
                    'url': media['media_url']
                }

        for url in tweet['entities'].get('urls', []):
            original = tweet['text'][url['indices'][0]:url['indices'][1]]
            replacement = '<a href="%s" target="_blank" onclick="_gaq.push([\'_trackEvent\', \'%s\', \'featured-tweet-action\', \'link\', 0, \'%s\']);">%s</a>' % (url['url'], app_config.PROJECT_SLUG, tweet_url, url['display_url'])

            subs[original] = replacement

        for hashtag in tweet['entities'].get('hashtags', []):
            original = tweet['text'][hashtag['indices'][0]:hashtag['indices'][1]]
            replacement = '<a href="https://twitter.com/hashtag/%s" target="_blank" onclick="_gaq.push([\'_trackEvent\', \'%s\', \'featured-tweet-action\', \'hashtag\', 0, \'%s\']);">%s</a>' % (hashtag['text'], app_config.PROJECT_SLUG, tweet_url, '#%s' % hashtag['text'])

            subs[original] = replacement

        for original, replacement in subs.items():
            html =  html.replace(original, replacement)

        # https://dev.twitter.com/docs/api/1.1/get/statuses/show/%3Aid
        tweets.append({
            'id': tweet['id'],
            'url': tweet_url,
            'html': html,
            'favorite_count': tweet['favorite_count'],
            'retweet_count': tweet['retweet_count'],
            'user': {
                'id': tweet['user']['id'],
                'name': tweet['user']['name'],
                'screen_name': tweet['user']['screen_name'],
                'profile_image_url': tweet['user']['profile_image_url'],
                'url': tweet['user']['url'],
            },
            'creation_date': creation_date,
            'photo': photo
        })

    # Facebook
    print 'Fetching Facebook posts...'

    fb_api = GraphAPI(secrets['FACEBOOK_API_APP_TOKEN'])

    facebook_posts = []

    for i in range(1, 4):
        fb_url = COPY['share']['featured_facebook%i' % i]

        if isinstance(fb_url, copytext.Error) or unicode(fb_url).strip() == '':
            continue

        fb_id = unicode(fb_url).split('/')[-1]

        post = fb_api.get_object(fb_id)
        user  = fb_api.get_object(post['from']['id'])
        user_picture = fb_api.get_object('%s/picture' % post['from']['id'])
        likes = fb_api.get_object('%s/likes' % fb_id, summary='true')
        comments = fb_api.get_object('%s/comments' % fb_id, summary='true')
        #shares = fb_api.get_object('%s/sharedposts' % fb_id)

        creation_date = datetime.strptime(post['created_time'],'%Y-%m-%dT%H:%M:%S+0000')
        creation_date = '%s %i' % (creation_date.strftime('%b'), creation_date.day)

        # https://developers.facebook.com/docs/graph-api/reference/v2.0/post
        facebook_posts.append({
            'id': post['id'],
            'message': post['message'],
            'link': {
                'url': post['link'],
                'name': post['name'],
                'caption': (post['caption'] if 'caption' in post else None),
                'description': post['description'],
                'picture': post['picture']
            },
            'from': {
                'name': user['name'],
                'link': user['link'],
                'picture': user_picture['url']
            },
            'likes': likes['summary']['total_count'],
            'comments': comments['summary']['total_count'],
            #'shares': shares['summary']['total_count'],
            'creation_date': creation_date
        })

    # Render to JSON
    output = {
        'tweets': tweets,
        'facebook_posts': facebook_posts
    }

    with open('data/featured.json', 'w') as f:
        json.dump(output, f)

class Book(object):
    """
    A single book instance.
    __init__ cleans the data.
    """
    isbn = None
    isbn13 = None
    hide_ibooks = False
    title = None
    author = None
    genre = None
    reviewer = None
    text = None
    slug = None
    tags = None
    book_seamus_id = None

    author_seamus_id = None
    author_seamus_headline = None

    review_seamus_id = None
    review_seamus_headline = None

    def __unicode__(self):
        """
        Returns a pretty value.
        """
        return self.title

    def __init__(self, **kwargs):
        """
        Cleans the ID fields coming back from the spreadsheet.
        Removes non-integer junk from the cells.
        Serializes based on commas.
        """
        print 'Processing %s (http://www.npr.org/%s)' % (kwargs['title'], kwargs['book_seamus_id'])
        for key, value in kwargs.items():

            # Kill smart quotes in fields
            value = value.replace('“','"').replace('”','"')
            value = value.replace('’', "'")

            # Handle wacky characters.
            value = unicode(value.decode('utf-8')).strip()

            if key == 'text':
                if value == '' or value == None:
                    print 'ERROR: Missing review for %s.' % kwargs['title']

            # Look up coverage
            if key == 'book_seamus_id' and value:
                r = requests.get('http://www.npr.org/%s' % value)
                try:
                    soup = BeautifulSoup(r.content)
                    items = soup.select('.storylist article')
                    item_list = []
                    if len(items):
                        for item in items:
                            link = {
                                'category': '',
                                'title': item.select('.title')[0].text.strip(),
                                'url': item.select('a')[0].attrs.get('href'),
                            }
                            category_elements = item.select('.slug')
                            if len(category_elements):
                                link['category'] = category_elements[0].text.strip()
                            item_list.append(link)
                            print 'LOG: Adding link: %(category)s: %(title)s (%(url)s)' % link

                    else:
                        print 'LOG: No links found for %s' % kwargs['title']
                    setattr(self, 'links', item_list)
                except:
                    print "SOMETHING WENT WRONG!!!"
            elif key == 'book_seamus_id' and not value:
                print 'ERROR: No seamus book id for %s' % kwargs['title']


            if key == 'isbn':
                value = value.zfill(10)

            if key == 'tags':
                # Build the empty list, since each can have more than one.
                item_list = []

                # Split on commas.
                for item in value.split(','):

                    # If it's not blank, add to the list.
                    # Returning an empty list is better than a blank
                    # string inside the list.
                    if item != u"":

                        # Clean.
                        item = item.strip().replace(' and ', ' & ')

                        # Look up from our map.
                        tag_slug = TAGS_TO_SLUGS.get(item.lower(), None)

                        # Append if the tag exists.
                        if tag_slug:
                            item_list.append(tag_slug)
                        else:
                            print "ERROR: Unknown tag: '%s'" % item

                # Set the attribute with the corrected value, which is a list.
                setattr(self, key, item_list)
            else:
                # Don't modify the value for stuff that isn't in the list above.
                setattr(self, key, value)

        # Calculate ISBN-13, see: http://www.ehow.com/how_5928497_convert-10-digit-isbn-13.html
        if self.isbn.startswith('978'):
            self.isbn13 = self.isbn
            print 'ISBN already 13 digits (%s)' % self.isbn
        else:
            isbn = '978%s' % self.isbn[:9]
            sum_even = 3 * sum(map(int, [isbn[1], isbn[3], isbn[5], isbn[7], isbn[9], isbn[11]]))
            sum_odd = sum(map(int, [isbn[0], isbn[2], isbn[4], isbn[6], isbn[8], isbn[10]]))
            remainder = (sum_even + sum_odd) % 10
            check = 10 - remainder if remainder else 0
            self.isbn13 = '%s%s' % (isbn, check)
            print 'LOG: Converted ISBN-10 (%s) to ISBN-13 (%s)' % (self.isbn, self.isbn13)

        # Slugify.
        slug = self.title.lower()
        slug = re.sub(r"[^\w\s]", '', slug)
        slug = re.sub(r"\s+", '-', slug)
        self.slug = slug[:254]

        print ''

def get_books_csv():
    """
    Downloads the books CSV from google docs.
    """
    csv_url = "https://docs.google.com/spreadsheet/pub?key=%s&single=true&gid=0&output=csv" % (
        app_config.DATA_GOOGLE_DOC_KEY)
    r = requests.get(csv_url)

    with open('data/books.csv', 'wb') as writefile:
        writefile.write(r.content)

def get_tags():
    """
    Extract tags from COPY doc.
    """
    print 'Extracting tags from COPY'

    book = xlrd.open_workbook(app_config.COPY_PATH)

    sheet = book.sheet_by_name('tags')

    for i in range(1, sheet.nrows):
        slug, tag = sheet.row_values(i)

        slug = slug.strip()
        tag = tag.replace(u'’', "'").strip()

        SLUGS_TO_TAGS[slug] = tag
        TAGS_TO_SLUGS[tag.lower()] = slug

def parse_books_csv():
    """
    Parses the books CSV to JSON.
    Creates book objects which are cleaned and then serialized to JSON.
    """
    get_tags()

    # Open the CSV.
    with open('data/books.csv', 'rb') as readfile:
        books = list(csv.DictReader(readfile))

    print "Start parse_books_csv(): %i rows." % len(books)

    book_list = []

    for book in books:

        # Skip books with no title or ISBN
        if book['title'] == "":
            continue

        if book['isbn'] == "":
            continue

        # Init a book class, passing our data as kwargs.
        # The class constructor handles cleaning of the data.
        b = Book(**book)

        # Grab the dictionary representation of a book.
        book_list.append(b.__dict__)

    # Dump the list to JSON.
    with open('www/static-data/books.json', 'wb') as writefile:
        writefile.write(json.dumps(book_list))

    print "End."

@task
def load_books():
    """
    Loads/reloads just the book data.
    Does not save image files.
    """
    get_books_csv()
    parse_books_csv()

@task
def load_images():
    """
    Downloads images from Baker and Taylor.
    Eschews the API for a magic URL pattern, which is faster.
    """

    # Secrets.
    secrets = app_config.get_secrets()

    # Open the books JSON.
    with open('www/static-data/books.json', 'rb') as readfile:
        books = json.loads(readfile.read())

    print "Start load_images(): %i books." % len(books)

    # Loop.
    for book in books:

        # Skip books with no title or ISBN.
        if book['title'] == "":
            continue

        if 'isbn' not in book or book['isbn'] == "":
            continue

        # Construct the URL with secrets and the ISBN.
        book_url = "http://imagesa.btol.com/ContentCafe/Jacket.aspx?UserID=%s&Password=%s&Return=T&Type=L&Value=%s" % (
            secrets['BAKER_TAYLOR_USERID'],
            secrets['BAKER_TAYLOR_PASSWORD'],
            book['isbn'])

        # Request the image.
        r = requests.get(book_url)

        path = 'www/assets/cover/%s.jpg' % book['slug']

        # Write the image to www using the slug as the filename.
        with open(path, 'wb') as writefile:
            writefile.write(r.content)

        file_size = os.path.getsize(path)

        if file_size < 10000:
            print "Image not available for ISBN: %s" % book['isbn']

        image = Image.open(path)

        width = 250
        height = int((float(width) / image.size[0]) * image.size[1])

        image.thumbnail([width, height], Image.ANTIALIAS)
        image.save(path.replace('.jpg', '-thumb.jpg'), 'JPEG')

    print "End."
