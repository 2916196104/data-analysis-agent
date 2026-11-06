"""Actual oversized stdout for testing the parent pipe bound independently."""
import os
import time

while True:
    os.write(1,b'x'*8192)
    time.sleep(0.001)
