"""Grab a webcam frame of the mask: python snap.py out.jpg [--crop x0,y0,x1,y1] [--scale 0.6]"""
import argparse
import time

import cv2

ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default="snap.jpg")
ap.add_argument("--crop", default=None, help="x0,y0,x1,y1 in full-frame pixels")
ap.add_argument("--scale", type=float, default=1.0)
a = ap.parse_args()

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
for _ in range(25):  # let exposure settle
    ok, frame = cap.read()
    time.sleep(0.04)
cap.release()
if not ok:
    raise SystemExit("no frame")
if a.crop:
    x0, y0, x1, y1 = map(int, a.crop.split(","))
    frame = frame[y0:y1, x0:x1]
if a.scale != 1.0:
    frame = cv2.resize(frame, None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_AREA)
cv2.imwrite(a.out, frame)
print(a.out)
