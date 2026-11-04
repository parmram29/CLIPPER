import shutil

import pytest

from clipper import cli

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_doctor_check_ai_makes_one_real_call_to_the_configured_endpoint(fake_llm, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # no cookies.txt / .env here
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-doctor-1234")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-plus")
    monkeypatch.delenv("CLIPPER_COOKIES", raising=False)
    fake_llm.reply = "ok"

    code = cli.main(["doctor", "--check-ai"])
    out = capsys.readouterr().out

    assert "[FAIL]" not in out, out
    assert code == 0
    assert "AI endpoint answered: 'ok'" in out
    assert "key ...1234" in out and "sk-doctor-1234" not in out  # the key is never printed
    (req,) = fake_llm.requests
    assert req["auth"] == "Bearer sk-doctor-1234"
    assert req["body"]["max_tokens"] == 5  # tiny, cheap probe


@needs_ffmpeg
def test_doctor_reports_a_failing_endpoint_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-x")
    monkeypatch.setenv("CLIPPER_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens on port 9
    code = cli.main(["doctor", "--check-ai"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[FAIL] AI endpoint call failed" in out


@needs_ffmpeg
def test_doctor_flags_missing_native_libs_for_face_tracking(monkeypatch, tmp_path, capsys):
    # mediapipe imports fine without libEGL; only creating the landmarker fails.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "mediapipe_available", lambda: True)
    monkeypatch.setattr(cli, "_face_model_path", lambda: tmp_path / "face_landmarker.task")

    def boom(_path):
        raise OSError("libEGL.so.1: cannot open shared object file: No such file or directory")

    monkeypatch.setattr(cli, "_probe_face_landmarker", boom)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0  # centered reframe still works, so this is a warning
    assert "[WARN] face tracking unavailable" in out
    assert "libEGL.so.1" in out and "apt install libegl1 libgles2" in out


@needs_ffmpeg
def test_doctor_confirms_face_tracking_when_the_runtime_loads(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "mediapipe_available", lambda: True)
    monkeypatch.setattr(cli, "_face_model_path", lambda: tmp_path / "face_landmarker.task")
    monkeypatch.setattr(cli, "_probe_face_landmarker", lambda _path: None)
    cli.main(["doctor"])
    assert "[ok  ] face tracking runtime works" in capsys.readouterr().out


@needs_ffmpeg
def test_doctor_without_config_only_warns(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for var in ("CLIPPER_API_KEY", "DASHSCOPE_API_KEY", "CLIPPER_COOKIES"):
        monkeypatch.delenv(var, raising=False)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0  # missing key / cookies are warnings, not failures
    assert "No API key configured" in out and "cookies file not found" in out
