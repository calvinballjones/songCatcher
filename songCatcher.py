from __future__ import unicode_literals
from datetime import datetime
import ConfigParser
import urllib2
import os

import feedparser
from boto.s3.key import Key
from dateutil import parser as date_parser
from bs4 import BeautifulSoup
import pytz
import youtube_dl
from boto.s3.connection import S3Connection
from feedgen.feed import FeedGenerator


__author__ = 'feanor93'

config = ConfigParser.ConfigParser()
fg = FeedGenerator()
fg.load_extension('podcast')


def get_config(section, item):
    # Reads the config file
    config.read("./songCatcher.config")
    return config.get(section, item)


aws_access_key = get_config("AWS Settings", "AWS ACCESS KEY ID")
aws_secret_key = get_config("AWS Settings", "AWS SECRET ACCESS KEY")

con = S3Connection(aws_access_key, aws_secret_key)


def make_soup(url):
    print "Downloading and scraping %s" % url
    page_xhtml = urllib2.urlopen(url).read()
    soup = BeautifulSoup(page_xhtml)
    return soup


def get_blog_posts(url, new_posts, last_scrape_datetime):
    # Getting the page file and parsing it with BeautifulSoup
    soup = make_soup(url)

    next_page_url = soup.find(class_="blog-pager-older-link")['href']

    for post in soup.find_all(class_="blog-post"):
        post_title = post.find(class_="post hentry").find(class_="post-header").h3.a.string
        post_url = post.find(class_="post hentry").find(class_="post-header").h3.a['href']
        post_created = post.find(class_="post hentry").find(class_="post-footer").find(class_="post-footer-meta") \
            .find(class_="post-timestamp").find(class_="timestamp-link").find(class_='published')['title']
        post_created_datetime = date_parser.parse(post_created)
        post_tags = post.find_all(rel="tag")
        post_tags_content = []
        music_of_the_day = False
        for tag in post_tags:
            post_tags_content.append(tag.string)

        if post_created_datetime <= last_scrape_datetime:
            return False, next_page_url

        if "News" in post_tags_content or "Music: Background" in post_tags_content:
            continue

        if "Music of the Day" in post_tags_content:
            music_of_the_day = True
        new_music_post = MusicPost(post_title, post_url, post_created_datetime, music_of_the_day)
        new_posts.append(new_music_post)

    return True, next_page_url


def scrape_youtube_links(url, youtube_links, music_of_the_day, music_of_the_day_setting, post_created):
    soup = make_soup(url)
    if music_of_the_day:
        if music_of_the_day_setting == "False":
            return
        anchors = soup.find_all('a')

        for a in anchors:
            if a.string and "Source" in a.string:
                youtube_links.append(YoutubeLink(a['href'], post_created))
    else:
        # return
        iframes = soup.find_all('iframe')

        for frame in iframes:
            link = frame['src'].replace("https://www.youtube.com/embed/", "")
            link = "https://www.youtube.com/watch?v=%s" % link
            youtube_links.append(YoutubeLink(link, post_created))


def main():
    # Get the time of this scrape
    current_scrape_time = datetime.now()
    print "Current Scrape Time: %s" % current_scrape_time
    # Getting the feed url
    feed_url = get_config("Feed Settings", "Feed")
    # Getting the most recent scrape parameter from the config and parsing it into a datetime object
    last_scrape = get_config("Feed Settings", "Most Recent Scrape")
    last_scrape_datetime = pytz.utc.localize(date_parser.parse(last_scrape))
    music_of_the_day_setting = get_config("Feed Settings", "Music of the Day")
    temp_directory_path = get_config("Feed Settings", "Temp Directory")

    new_music_posts = []
    new_posts = True
    current_url = feed_url

    youtube_links = []
    music_files = []

    # Get a list of music blog posts that have not been scraped yet
    while new_posts:
        new_posts, current_url = get_blog_posts(current_url, new_music_posts, last_scrape_datetime)

    # Scrape each post and add the youtube links to the youtube_links list
    for post in new_music_posts:
        scrape_youtube_links(post.post_url, youtube_links, post.music_of_the_day, music_of_the_day_setting,
                             post.post_created)

    # Check for a temporary directory and create it if it doesn't exist
    if not os.path.isdir(temp_directory_path):
        print "Making a temp directory at %s" % temp_directory_path
        os.makedirs(temp_directory_path)
    os.chdir(temp_directory_path)

    # Download each youtube link to the temp directory, saving it's file location to the a MusicFile object
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
                           'key': 'FFmpegExtractAudio',
                           'preferredcodec': 'mp3',
                           'preferredquality': '192',
                           }],
        'quiet': True,
        'outtmpl': '%(id)s.%(ext)s'
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        for link in youtube_links:
            result = ydl.extract_info(link.link_url, download=False)

            print "Youtube-dl is downloading %s | Number %s out of %s" % (link.link_url, youtube_links.index(link)+1,
                                                                          len(youtube_links))
            ydl.download([link.link_url])

            file_name = "%s.mp3" % result['id']
            path = "./%s" % file_name
            new_music_file = MusicFile(song_youtube_url=link.link_url,
                                       song_post_published=link.link_post_published,
                                       song_file_name=file_name,
                                       song_path=path,
                                       song_title=result['title'],
                                       song_artist=result['uploader'])
            music_files.append(new_music_file)

    # Upload each new song to S3, saving its url in the MusicFile object
    song_catcher_bucket = con.get_bucket("songcatcher")
    for song in music_files:
        k = Key(song_catcher_bucket)
        k.key = "media/%s" % song.song_file_name
        if not song_catcher_bucket.get_key(k.key):
            print "Uploading %s to %s" % (song.song_title, k.key)
            k.set_contents_from_filename(song.song_path)
        else:
            print "File %s already exists at %s" % (song.song_title, k.key)
        song_aws_url = "https://s3-us-west-2.amazonaws.com/songcatcher/media/%s" % song.song_file_name
        song.song_aws_url = song_aws_url

    # Download the most recent RSS feed from S3 and parse into objects
    rss_file = song_catcher_bucket.get_key("songcatcher.xml")
    if rss_file:
        print "RSS file found"
        d = feedparser.parse(rss_file.get_contents_as_string())
        music_file_ids = []
        for f in music_files:
            print "Appending %s to music_file_ids" % f.song_aws_url
            music_file_ids.append(f.song_aws_url)
        for entry in d.entries:
            print "entry.id is %s" % entry.id
            if entry.id not in music_file_ids:
                print "entry %s IS NOT in music_file_ids" % entry.id
                print "entry.author_detail: %s" % entry.author_detail
                music_files.append(MusicFile(song_youtube_url=entry.guid,
                                             song_post_published=entry.published,
                                             song_title=entry.title,
                                             song_artist=entry.author_detail.name,
                                             song_aws_url=entry.enclosures[0]['href']
                                             )
                                   )
            else:
                print "entry %s IS in music_file_ids" % entry.id

    # Add the new MusicFile objects to the rss feed objects
    fg.id("https://s3-us-west-2.amazonaws.com/songcatcher/songcatcher.xml")
    fg.title("SongCatcher")
    fg.author({'name': 'Calvinball Jones', 'email': 'calvinballjones@gmail.com'})
    fg.link(href='https://s3-us-west-2.amazonaws.com/songcatcher/songcatcher.xml', rel='self')
    fg.language('en')
    fg.description("New songs from Equestria Daily!")

    for song in music_files:
        fe = fg.add_entry()
        fe.id(song.song_aws_url)
        fe.title(song.song_title)
        fe.author({'name': song.song_artist, 'email': 'calvinballjones@gmail.com'})
        fe.link(href=song.song_youtube_url, rel='self')
        fe.enclosure(song.song_aws_url, 0, 'audio/mpeg')
        fe.published(song.song_post_published)
        fe.description("%s by %s" % (song.song_title, song.song_artist))

    # Create a new RSS feed file and upload it
    fg.rss_str(pretty=True)
    fg.rss_file('songcatcher.xml')
    k = Key(song_catcher_bucket)
    k.key = "songcatcher.xml"
    k.set_contents_from_filename('songcatcher.xml')

    # Delete songs in the temp directory
    for f in os.listdir('.'):
        os.remove(f)

    # Update the Most Recent Scrape in the config file
    raw_config = ConfigParser.RawConfigParser()
    raw_config.read("../songCatcher.config")
    raw_config.set("Feed Settings", "Most Recent Scrape", current_scrape_time)
    raw_config.write(file("../songCatcher.config", 'w'))


class MusicPost:
    def __init__(self, post_title, post_url, post_created, music_of_the_day):
        self.post_title = post_title
        self.post_url = post_url
        self.post_created = post_created
        self.music_of_the_day = music_of_the_day

    def __unicode__(self):
        return self.post_title


class MusicFile:
    def __init__(self, song_youtube_url, song_post_published, song_file_name=None, song_path=None,
                 song_title="Unknown Song", song_artist="Unknown Artist", song_aws_url=None):
        self.song_youtube_url = song_youtube_url
        self.song_post_published = song_post_published
        self.song_file_name = song_file_name
        self.song_path = song_path
        self.song_title = song_title
        self.song_artist = song_artist
        self.song_aws_url = song_aws_url

    def __unicode__(self):
        return self.song_title


class YoutubeLink:
    def __init__(self, link_url, link_post_published):
        self.link_url = link_url
        self.link_post_published = link_post_published

    def __unicode__(self):
        return self.link_url


if __name__ == '__main__':
    main()