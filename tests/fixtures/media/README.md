# Test fixtures, not episode media

Synthetic files for exercising the model gateway's image, audio and video
paths. They are unrelated to *Aakhri Chitthi* and must never be used or
reported as episode evidence.

| File | Made with |
|---|---|
| `test_frame.jpg` | `ffmpeg -f lavfi -i testsrc=size=64x64:rate=1 -frames:v 1 -q:v 5 test_frame.jpg` |
| `test_audio.wav` | `ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -ar 8000 -ac 1 -c:a pcm_s16le test_audio.wav` |
| `test_video.mp4` | `ffmpeg -f lavfi -i testsrc=size=64x64:rate=10 -t 1 -pix_fmt yuv420p -c:v libx264 -an test_video.mp4` |
