"""Finish takes: `post.py clip <raw.mp4> <out.mp4>` / `post.py combine <out.mp4> <clip.mp4>...` (appdrive.post)."""
import maple_client  # noqa: F401  (puts appdrive on the path when it is not installed)
from appdrive.post import main

if __name__ == "__main__":
    main()
