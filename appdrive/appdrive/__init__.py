"""appdrive: drive a Windows app window through cua-driver (capture, OCR, input, takes, recording)."""

from .perception import match_text, only_match, perceive, region_center
from .takes import Recorder, Step, TakeContext, TakeRunner
from .window import AppConfig, AppWindow, create_driver, list_windows, open_window, poll, window_matcher

__all__ = ["AppConfig", "AppWindow", "Recorder", "Step", "TakeContext", "TakeRunner", "create_driver",
           "list_windows", "match_text", "only_match", "open_window", "perceive", "poll", "region_center",
           "window_matcher"]
