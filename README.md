SongCatcher
===========

SongCatcher is a Python script that scrapes [the Equestria Daily website](http://equestriadaily.com) for posts 
containing songs, downloads the YouTube videos, uploads them to an S3 Bucket, and turns them into a personal podcast 
feed.  

To get started, rename `songCatcher.config.default` to `songCatcher.config` and replace the *aws access key id* and 
*aws secret access key* with your own AWS account information. You'll also need to create an S3 bucket and add that 
information to the config file.

To run the script, just run `python songCatcher.py`.