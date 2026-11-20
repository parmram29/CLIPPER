"""Rendering a clip used to cost a MacBook Air about 20 minutes: the "download" re-encoded the whole section at ffmpeg's slow
defaults, the portrait layout blurred a full 1080x1920 picture 60 times a second, and the hook and the captions each re-encoded
the clip again, all on the CPU (an Intel Mac was never offered its hardware encoder). These tests pin the fixes, using real
ffmpeg and the real yt-dlp wherever the point is what they actually do."""

import functools
import http.server
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from clipper import caption_generator, clip_processor, encoding, gpu, hook_generator, portrait, video_processor, watermark

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

VT = ("-c:v", "h264_videotoolbox", "-b:v", "10M", "-profile:v", "high")


def fake_gpu(monkeypatch, name, preset=None, available=True, reason="pretend"):
    monkeypatch.setattr(
        gpu, "detect_gpu",
        lambda: {"gpu": {"type": "x", "name": "a pretend GPU", "available": available},
                 "encoder": {"name": name if available else None, "preset": preset, "available": available, "reason": reason}},
    )


# --- a Mac is offered its encoder, Intel ones included -------------------------------------------------------------------------


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(gpu, "_is_mac", lambda: True)
    monkeypatch.setattr(gpu, "get_ffmpeg_path", lambda: "ffmpeg")

    def boom():  # the slow system report that upstream used to read the graphics card's name must not be needed
        raise AssertionError("a Mac's encoder must not depend on recognising its graphics card")

    monkeypatch.setattr(gpu, "_detect_gpu_hardware", boom)
    gpu.reset_detection()
    return monkeypatch


def test_a_mac_uses_videotoolbox_whatever_its_graphics_card_is_called(mac):
    probes = []
    mac.setattr(gpu, "_available_encoders", lambda path: ["h264_videotoolbox"])
    mac.setattr(gpu, "_probe_encoder", lambda path, name, timeout=20, extra_args=(): probes.append((name, extra_args)) or (True, None))
    found = gpu.detect_gpu()
    assert found["encoder"]["name"] == "h264_videotoolbox" and found["encoder"]["available"] is True
    assert probes == [("h264_videotoolbox", gpu.VIDEOTOOLBOX_ARGS)]  # tried with the settings a real encode uses


def test_a_mac_without_the_encoder_in_its_ffmpeg_falls_back_and_says_why(mac):
    mac.setattr(gpu, "_available_encoders", lambda path: ["libx264"])
    found = gpu.detect_gpu()["encoder"]
    assert found["available"] is False and "h264_videotoolbox" in found["reason"]


def test_a_mac_whose_encoder_will_not_start_falls_back_and_says_why(mac):
    mac.setattr(gpu, "_available_encoders", lambda path: ["h264_videotoolbox"])
    mac.setattr(gpu, "_probe_encoder", lambda *a, **k: (False, "Cannot create compression session"))
    found = gpu.detect_gpu()["encoder"]
    assert found["available"] is False and "Cannot create compression session" in found["reason"]


def test_detection_is_remembered_and_can_be_forgotten(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_is_mac", lambda: False)
    monkeypatch.setattr(gpu, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(gpu, "_detect_gpu_hardware", lambda: calls.append(1) or {"type": None, "name": "none", "available": False})
    gpu.reset_detection()
    first = gpu.detect_gpu()
    assert gpu.detect_gpu() is first and calls == [1]  # every step of every clip asks; the machine is only examined once
    gpu.reset_detection()
    gpu.detect_gpu()
    assert calls == [1, 1]


def test_videotoolbox_is_given_a_bitrate_because_its_default_is_unwatchable(monkeypatch):
    fake_gpu(monkeypatch, "h264_videotoolbox")
    chosen = encoding.choose()
    assert chosen.hardware and chosen.args == VT
    assert "-b:v" in chosen.args and "10M" in chosen.args


# --- which encoder, and what happens when it fails -------------------------------------------------------------------------------


def test_without_hardware_the_cpu_encoder_is_the_quicker_veryfast(monkeypatch):
    fake_gpu(monkeypatch, None, available=False, reason="No GPU detected")
    chosen = encoding.choose()
    assert not chosen.hardware and chosen.args == ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20")
    assert "No GPU detected" in chosen.reason


def test_other_vendors_encoders_keep_the_settings_they_had(monkeypatch):
    fake_gpu(monkeypatch, "h264_nvenc", preset="p4")
    assert encoding.choose().args == ("-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23")
    fake_gpu(monkeypatch, "h264_qsv", preset="faster")
    assert encoding.choose().args == ("-c:v", "h264_qsv", "-preset", "faster")


def test_a_failing_hardware_encoder_is_retried_on_the_cpu_and_not_tried_again(monkeypatch):
    fake_gpu(monkeypatch, "h264_videotoolbox")
    seen, logs = [], []

    def call(command):
        seen.append(command)
        failed = "h264_videotoolbox" in command
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "Error: cannot create compression session\n" if failed else "")

    monkeypatch.setattr(encoding, "_call", call)
    result = encoding.run(lambda enc: ["ffmpeg", *enc, "out.mp4"], log=logs.append)

    assert result.returncode == 0
    assert seen[0][1:3] == ["-c:v", "h264_videotoolbox"] and seen[1][1:3] == ["-c:v", "libx264"]
    assert any("h264_videotoolbox encoder failed" in l and "cannot create compression session" in l for l in logs)
    # the next step goes straight to the CPU encoder: no failed attempt first
    seen.clear()
    encoding.run(lambda enc: ["ffmpeg", *enc, "out.mp4"], log=logs.append)
    assert len(seen) == 1 and seen[0][1:3] == ["-c:v", "libx264"]
    assert "failed earlier" in encoding.choose().reason


def test_a_failing_cpu_encode_is_not_retried(monkeypatch):
    fake_gpu(monkeypatch, None, available=False)
    seen = []
    monkeypatch.setattr(encoding, "_call", lambda c: seen.append(c) or subprocess.CompletedProcess(c, 1, "", "no"))
    assert encoding.run(lambda enc: ["ffmpeg", *enc], log=lambda m: None).returncode == 1
    assert len(seen) == 1


def test_the_encoder_is_announced_once_per_run_not_once_per_step(monkeypatch):
    fake_gpu(monkeypatch, "h264_videotoolbox", reason="Using this Mac's built-in video encoder")
    monkeypatch.setattr(encoding, "_call", lambda c: subprocess.CompletedProcess(c, 0, "", ""))
    logs = []
    for _ in range(4):
        encoding.run(lambda enc: ["ffmpeg", *enc], log=logs.append)
    assert [l for l in logs if "encoder" in l.lower()] == ["Using hardware encoder: h264_videotoolbox (Using this Mac's built-in video encoder)"]


# --- how much there is to encode ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "setting, expected",
    [(None, 30.0), ("", 30.0), ("60", 60.0), ("24", 24.0), ("off", 0.0), ("0", 0.0), ("Source", 0.0), ("abc", 30.0), ("1", 30.0), ("500", 30.0)],
)
def test_the_frame_rate_cap(setting, expected):
    assert encoding.max_fps({} if setting is None else {"CLIPPER_MAX_FPS": setting}) == expected


@pytest.mark.parametrize("source, cap, expected", [(59.94, 30, "fps=30"), (60, 30, "fps=30"), (30, 30, None), (30.02, 30, None), (25, 30, None), (24, 30, None), (60, 0, None), (60, 60, None)])
def test_only_a_faster_source_is_thinned_out_never_a_slower_one_sped_up(source, cap, expected):
    assert encoding.fps_filter(source, cap) == expected


@pytest.mark.parametrize(
    "centered, setting, expected",
    [(True, None, 720), (False, None, 1080), (True, "1080", 1080), (False, "480", 480), (True, "junk", 720), (False, "100", 240), (True, "9999", 2160)],
)
def test_the_height_of_the_source_to_download(centered, setting, expected):
    assert encoding.max_height(centered, {} if setting is None else {"CLIPPER_MAX_HEIGHT": setting}) == expected


# --- the portrait layout, with the real ffmpeg --------------------------------------------------------------------------------


def make_video(path, seconds=3, fps=60, size="1280x720", audio=True):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-c:a", "aac"]
    subprocess.run([*cmd, "-t", str(seconds), "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return str(path)


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,codec_name,pix_fmt:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    num, den = data["streams"][0]["r_frame_rate"].split("/")
    return {**data["streams"][0], "fps": int(num) / int(den), "duration": float(data["format"]["duration"])}


def encodes(commands):
    """Only the portrait encodes among the recorded commands (the test's own helpers and the probes are recorded too)."""
    return [c for c in commands if "-progress" in c]


@pytest.fixture
def spy_commands(monkeypatch):
    """Record every process started with Popen, and still run it."""
    commands = []
    real = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(portrait.subprocess, "Popen", popen)
    return commands


@needs_ffmpeg
@pytest.mark.parametrize("background", ["blurred", "black"])
def test_a_60_fps_source_comes_out_at_30_fps_in_portrait_shape(tmp_path, spy_commands, background):
    src = make_video(tmp_path / "src.mp4")
    out = portrait.convert_to_portrait_centered(src, str(tmp_path / "out.mp4"), background=background, log=lambda m: None)
    info = probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and info["fps"] == 30 and info["pix_fmt"] == "yuv420p"
    assert abs(info["duration"] - 3.0) < 0.2  # thinning frames does not shorten the clip
    assert "fps=30" in " ".join(encodes(spy_commands)[0])


@needs_ffmpeg
def test_the_fps_cap_can_be_switched_off(tmp_path, spy_commands, monkeypatch):
    monkeypatch.setenv("CLIPPER_MAX_FPS", "off")
    src = make_video(tmp_path / "src.mp4")
    out = portrait.convert_to_portrait_centered(src, str(tmp_path / "out.mp4"), background="blurred", log=lambda m: None)
    assert probe(out)["fps"] == 60 and "fps=" not in " ".join(encodes(spy_commands)[0])


@needs_ffmpeg
def test_a_30_fps_source_is_left_alone(tmp_path, spy_commands):
    src = make_video(tmp_path / "src.mp4", fps=30)
    logs = []
    out = portrait.convert_to_portrait_centered(src, str(tmp_path / "out.mp4"), background="blurred", log=logs.append)
    assert probe(out)["fps"] == 30 and "fps=" not in " ".join(encodes(spy_commands)[0])
    assert not any("Keeping" in l for l in logs)


@needs_ffmpeg
def test_the_blurred_background_is_blurred_at_a_quarter_of_the_size(tmp_path, spy_commands):
    src = make_video(tmp_path / "src.mp4", seconds=1)
    portrait.convert_to_portrait_centered(src, str(tmp_path / "out.mp4"), background="blurred", log=lambda m: None)
    command = encodes(spy_commands)[0]
    graph = command[command.index("-filter_complex") + 1]
    blur = graph[graph.index("[bg]"): graph.index("[bgblur]")]
    assert "scale=270:480" in blur and "gblur=sigma=5" in blur and blur.endswith("scale=1080:1920:flags=bilinear")
    assert "1080:1920:force_original_aspect_ratio=increase" not in graph  # nothing is blurred at full size any more


@needs_ffmpeg
def test_the_blurred_picture_still_looks_like_a_blur_of_the_source(tmp_path):
    """The top rows of the portrait frame are pure background. A quarter-size blur must be as smooth as a full-size one:
    compare it with the picture the old full-size blur made of the same frame."""
    src = make_video(tmp_path / "src.mp4", seconds=1, fps=30)
    new = portrait.convert_to_portrait_centered(src, str(tmp_path / "new.mp4"), background="blurred", log=lambda m: None)
    old = tmp_path / "old.mp4"
    old_graph = (
        "[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=20[bgblur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgfit];[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2[v]"
    )
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-filter_complex", old_graph, "-map", "[v]", "-c:v", "libx264", "-crf", "18", str(old)], check=True)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", new, "-i", str(old), "-filter_complex", "[0:v]crop=1080:300:0:0[a];[1:v]crop=1080:300:0:0[b];[a][b]ssim", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    ssim = float(re.search(r"SSIM.*?All:([0-9.]+)", result.stderr)[1])
    assert ssim > 0.99, f"the quarter-size blur drifted from the full-size one (SSIM {ssim})"


@needs_ffmpeg
def test_a_hardware_encoder_that_fails_mid_run_is_replaced_by_the_cpu_for_every_step(tmp_path, monkeypatch, spy_commands):
    fake_gpu(monkeypatch, "h264_nosuchencoder")  # ffmpeg answers "Unknown encoder" and exits with an error
    logs = []
    src = make_video(tmp_path / "src.mp4", seconds=2)

    laid_out = portrait.convert_to_portrait_centered(src, str(tmp_path / "portrait.mp4"), background="blurred", log=logs.append)
    assert [c[c.index("-c:v") + 1] for c in encodes(spy_commands)] == ["h264_nosuchencoder", "libx264"]
    assert any("GPU encoder h264_nosuchencoder failed" in l for l in logs)
    assert probe(laid_out)["codec_name"] == "h264"

    # the hook and the captions then go straight to libx264, with no failed attempt in front
    runs = []
    real_call = encoding._call
    monkeypatch.setattr(encoding, "_call", lambda c: runs.append(c) or real_call(c))
    hooked = hook_generator.generate_hook(laid_out, "WAIT FOR IT", str(tmp_path / "hooked.mp4"), 1.0, {}, logs.append)
    words = [{"word": "hello", "start": 0.1, "end": 0.5}, {"word": "there", "start": 0.6, "end": 1.0}]
    captioned = caption_generator.generate_captions_from_words(hooked, str(tmp_path / "captioned.mp4"), words, logs.append)
    assert [c[c.index("-c:v") + 1] for c in runs] == ["libx264", "libx264"]
    assert probe(captioned)["codec_name"] == "h264" and probe(captioned)["pix_fmt"] == "yuv420p"


@needs_ffmpeg
def test_hook_captions_and_watermark_all_run_with_the_shared_encoder(tmp_path, monkeypatch):
    runs = []
    real_call = encoding._call
    monkeypatch.setattr(encoding, "_call", lambda c: runs.append(c) or real_call(c))
    src = make_video(tmp_path / "src.mp4", seconds=2, fps=30, size="540x960")
    hooked = hook_generator.generate_hook(src, "HOOK", str(tmp_path / "h.mp4"), 1.0, {}, lambda m: None)
    captioned = caption_generator.generate_captions_from_words(hooked, str(tmp_path / "c.mp4"), [{"word": "hi", "start": 0.1, "end": 0.6}], lambda m: None)
    marked = watermark.apply_watermark(captioned, str(tmp_path / "w.mp4"), None, {"text": "credit", "color": "#ffffff", "font_size": 24, "opacity": 1.0, "position_x": 0.1, "position_y": 0.9}, lambda m: None)
    assert len(runs) == 3
    for command in runs:
        assert command[command.index("-c:v") + 1] == "libx264" and "veryfast" in command and "fast" not in command
    assert (probe(marked)["width"], probe(marked)["height"]) == (540, 960)


# --- the cut out of the source video, with the real yt-dlp and ffmpeg ----------------------------------------------------------


class RangeServer(http.server.SimpleHTTPRequestHandler):
    """Static files with HTTP Range support, which is how a video host serves a piece of a file."""

    def do_GET(self):  # noqa: N802
        path = Path(self.directory) / self.path.lstrip("/")
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match:
            start = int(match[1] or 0)
            end = min(int(match[2]), size - 1) if match[2] else end
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            try:
                self.wfile.write(f.read(end - start + 1))
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, *args):
        pass


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A video whose brightness steps up every second (so any frame says what second of the source it came from),
    with a keyframe only every 2 seconds, served over HTTP the way a video host does."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100", "-t", "6",
         "-vf", "geq=lum='20+20*floor(T)':cb=128:cr=128", "-c:v", "libx264", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(root / "source.mp4")], check=True)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_COOKIES", str(cookies))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(RangeServer, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/source.mp4"
    server.shutdown()
    server.server_close()


def rgb_of_luma(luma):
    return (luma - 16) * 255 / 219


def first_frame_brightness(path):
    import cv2

    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return float(frame[:, :, :].mean())


@needs_ffmpeg
def test_the_section_is_cut_exactly_where_asked_not_at_the_keyframe_before_it(served, tmp_path):
    """The captions are timed from the first frame of the section. A cut that snaps back to the previous keyframe (as a fast
    stream copy does) would start up to 2 seconds early here and every caption would arrive that much late."""
    out = video_processor.download_video_section(served, "00:00:03.000", "00:00:05.000", str(tmp_path / "section.mp4"), lambda m: None, max_height=720)
    info = probe(out)
    assert abs(info["duration"] - 2.0) < 0.25
    assert info["codec_name"] == "h264" and info["pix_fmt"] == "yuv420p"
    # second 3 of the source has luma 20+20*3 = 80; second 2 (the keyframe before the cut) has 60. OpenCV shows video's
    # limited-range luma as full-range RGB: (luma - 16) * 255 / 219.
    seen = first_frame_brightness(out)
    assert abs(seen - rgb_of_luma(80)) < 5, f"the section starts at brightness {seen}, not at second 3"
    assert abs(seen - rgb_of_luma(60)) > 15, "the section starts at the keyframe before the cut"


@needs_ffmpeg
def test_the_cut_is_written_with_the_fast_encoder_settings(served, tmp_path, monkeypatch):
    import yt_dlp

    seen = {}
    real = yt_dlp.YoutubeDL

    class Spy(real):
        def __init__(self, params=None, *a, **k):
            seen.update(params or {})
            super().__init__(params, *a, **k)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Spy)
    video_processor.download_video_section(served, "00:00:01.000", "00:00:02.000", str(tmp_path / "s.mp4"), lambda m: None, max_height=720)
    assert seen["external_downloader_args"] == {"ffmpeg_o": list(encoding.CUT_ARGS)}
    assert seen["force_keyframes_at_cuts"] is True  # exactness is kept
    assert "-preset" in encoding.CUT_ARGS and encoding.CUT_ARGS[encoding.CUT_ARGS.index("-preset") + 1] == "ultrafast"


def capture_options(monkeypatch, outcomes):
    """Replace yt-dlp's downloader with one that records its options and fails or succeeds as told, one outcome per call."""
    import yt_dlp

    calls = []

    class Fake:
        def __init__(self, params=None, *a, **k):
            self.params = dict(params or {})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            calls.append(self.params)
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            Path(self.params["outtmpl"]).write_bytes(b"video")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Fake)
    monkeypatch.setattr(video_processor, "get_ffmpeg_path", lambda: shutil.which("ffmpeg") or "/bin/true")
    monkeypatch.setattr(video_processor, "_get_cookies_path", lambda: "cookies.txt")
    return calls


@pytest.mark.parametrize("cap", [720, 1080, 480])
def test_the_format_choice_is_capped_at_the_requested_height(tmp_path, monkeypatch, cap):
    calls = capture_options(monkeypatch, [None])
    video_processor.download_video_section("https://example.test/v", "00:00:01.000", "00:00:02.000", str(tmp_path / "s.mp4"), lambda m: None, max_height=cap)
    fmt = calls[0]["format"]
    assert f"height<={cap}" in fmt and all(h not in fmt for h in {"720", "1080", "480"} - {str(cap)})


def test_the_default_height_is_still_1080_as_upstream(tmp_path, monkeypatch):
    calls = capture_options(monkeypatch, [None])
    video_processor.download_video_section("https://example.test/v", "00:00:01.000", "00:00:02.000", str(tmp_path / "s.mp4"), lambda m: None)
    assert "height<=1080" in calls[0]["format"]


def test_if_ffmpeg_rejects_the_quick_settings_it_cuts_again_with_its_defaults_and_still_only_the_section(tmp_path, monkeypatch):
    calls = capture_options(monkeypatch, [RuntimeError("ERROR: ffmpeg exited with code 1"), None])
    logs = []
    video_processor.download_video_section("https://example.test/v", "00:00:01.000", "00:00:02.000", str(tmp_path / "s.mp4"), logs.append, max_height=720)
    assert len(calls) == 2
    assert "external_downloader_args" in calls[0] and "external_downloader_args" not in calls[1]
    assert "download_ranges" in calls[1] and calls[1]["force_keyframes_at_cuts"] is True  # not the whole video
    assert any("quick cut did not work" in l for l in logs)


def test_a_forbidden_error_is_reported_at_once_without_retrying(tmp_path, monkeypatch):
    calls = capture_options(monkeypatch, [RuntimeError("ffmpeg: HTTP error 403 Forbidden")])
    with pytest.raises(RuntimeError, match="cookies may have expired"):
        video_processor.download_video_section("https://example.test/v", "00:00:01.000", "00:00:02.000", str(tmp_path / "s.mp4"), lambda m: None)
    assert len(calls) == 1


def test_the_waiting_message_no_longer_claims_to_be_waiting_for_download_data():
    source = Path(video_processor.__file__).read_text(encoding="utf-8")
    assert "Still waiting for download data" not in source and "Cutting this section out of the video" in source


# --- the per-clip pipeline: the height, and the timing line ----------------------------------------------------------------------


def run_pipeline(tmp_path, monkeypatch, reframe, extra_options=None):
    seen = {}
    logs = []

    def cut(url, start_time, end_time, output_path, log=None, max_height=1080):
        seen["max_height"] = max_height
        Path(output_path).write_bytes(b"cut")
        return output_path

    monkeypatch.setattr(clip_processor, "download_video_section", cut)
    monkeypatch.setattr(clip_processor, "_run_portrait", lambda i, o, opts, log: i)
    session = tmp_path / "session"
    (session / "_temp").mkdir(parents=True)
    clip_processor.process_selected_highlights(
        "https://example.test/v",
        [{"title": "T", "start_time": "00:01:00,000", "end_time": "00:02:10,000", "hook_text": "", "_highlight_index": 0}],
        str(session), {"reframeMode": reframe, **(extra_options or {})}, {}, logs.append,
    )
    return seen, logs


@pytest.mark.parametrize("reframe, expected", [("centered", 720), ("face", 1080), ("original", 1080)])
def test_the_centered_layout_asks_for_720p_and_the_others_keep_1080p(tmp_path, monkeypatch, reframe, expected):
    seen, _ = run_pipeline(tmp_path, monkeypatch, reframe)
    assert seen["max_height"] == expected


def test_clippers_height_setting_overrides_the_layout_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_MAX_HEIGHT", "1080")
    seen, _ = run_pipeline(tmp_path, monkeypatch, "centered")
    assert seen["max_height"] == 1080


def test_each_clip_ends_with_a_line_saying_where_the_time_went(tmp_path, monkeypatch):
    _, logs = run_pipeline(tmp_path, monkeypatch, "centered")
    (line,) = [l for l in logs if "] Timing: " in l]
    assert re.fullmatch(r"\[1/1\] Timing: cut \d+s, layout \d+s \(\d+s in all\)", line), line
    assert logs.index(line) > next(i for i, l in enumerate(logs) if "Clip saved" in l)


def test_the_timing_line_does_not_disturb_the_apps_progress_bar():
    from clipper.gui import progress

    lines = ["Processing clip 1/2: A", "[1/2] Downloading video section 00:00:01 -> 00:01:10...", "[1/2] Section downloaded: x", "[1/2] Clip saved: y"]
    before = progress.summarize(lines, "process")
    after = progress.summarize([*lines, "[1/2] Timing: cut 41s, layout 1m05s (1m50s in all)"], "process")
    assert before["percent"] == after["percent"] and before["headline"] == after["headline"]


# --- the setup check says which encoder will be used -----------------------------------------------------------------------------


def test_doctor_says_which_video_encoder_will_be_used(monkeypatch, tmp_path, capsys):
    from clipper import cli

    monkeypatch.chdir(tmp_path)
    fake_gpu(monkeypatch, "h264_videotoolbox", reason="Using this Mac's built-in video encoder")
    cli.main(["doctor"])
    assert "[ok  ] video encoder: h264_videotoolbox, built into this computer (Using this Mac's built-in video encoder)" in capsys.readouterr().out

    encoding.reset()
    fake_gpu(monkeypatch, None, available=False, reason="No GPU detected — will use CPU (libx264)")
    cli.main(["doctor"])
    assert "[WARN] video encoder: libx264 on the CPU, which is slow on a laptop (No GPU detected" in capsys.readouterr().out
