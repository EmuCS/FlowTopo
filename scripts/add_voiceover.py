"""Add the recorded voice-over blocks to the demo video.

blender --background demo/flowtopo_demo_edit.blend --python scripts/add_voiceover.py
Reads demo/vo/01.* ... 17.* (one file per VIDEO_SCRIPT.md block, any audio format Blender reads),
places each at its block's start, writes demo/flowtopo_demo_vo.mp4.
"""
import os

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VO = os.path.join(ROOT, "demo", "vo")
STARTS = [0, 7, 14, 21, 37, 43, 51, 60, 67, 74, 84, 93, 102, 111, 121, 131, 139]  # seconds, VIDEO_SCRIPT.md

scene = bpy.context.scene
fps = scene.render.fps / scene.render.fps_base
seq = scene.sequence_editor
strips = seq.strips if hasattr(seq, "strips") else seq.sequences
files = sorted((f for f in os.listdir(VO) if os.path.splitext(f)[0].isdigit()), key=lambda f: int(os.path.splitext(f)[0]))
prev_end = 0
for f in files:
    k = int(os.path.splitext(f)[0])
    if not 1 <= k <= len(STARTS):
        continue
    start = max(scene.frame_start + int(round(STARTS[k - 1] * fps)), prev_end + 4)  # never talk over the previous block
    s = strips.new_sound(name="vo_%02d" % k, filepath=os.path.join(VO, f), channel=30 + k % 2, frame_start=start)
    prev_end = s.frame_final_end
    end = STARTS[k] if k < len(STARTS) else (scene.frame_end + 1 - scene.frame_start) / fps
    over = s.frame_final_duration / fps - (end - STARTS[k - 1])
    print("VO %02d at %5.1fs (planned) %5.1fs (actual)" % (k, STARTS[k - 1], (start - scene.frame_start) / fps)); print("VO %02d at %5.1fs, %4.1fs long%s" % (k, STARTS[k - 1], s.frame_final_duration / fps,
                                                "  OVERRUNS by %.1fs" % over if over > 0.3 else ""))
scene.render.ffmpeg.audio_codec = "AAC"
scene.render.ffmpeg.audio_bitrate = 192
scene.render.ffmpeg.audio_channels = "STEREO"
scene.render.filepath = os.path.join(ROOT, "demo", "flowtopo_demo_vo.mp4")
bpy.ops.render.render(animation=True)
print("VIDEO", scene.render.filepath)
