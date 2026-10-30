"""Measure how often the mask changes frame, from the webcam: python frame_timer.py [seconds]"""
import sys
import time

import cv2
import numpy as np

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
for _ in range(10):
    cap.read()
prev, t0, hits, n = None, time.time(), [], 0
while time.time() - t0 < secs:
    ok, f = cap.read()
    n += 1
    g = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY).astype(float)
    if prev is not None and np.abs(g - prev).mean() > 2.5:
        hits.append(time.time() - t0)
    prev = g
cap.release()
events = hits[:1] + [b for a, b in zip(hits, hits[1:]) if b - a > 0.15]
gaps = np.diff(events) if len(events) > 1 else []
print(f"camera {n / secs:.1f} fps, {len(events)} changes, median interval {np.median(gaps):.2f}s" if len(gaps) else f"camera {n / secs:.1f} fps, too few changes")
