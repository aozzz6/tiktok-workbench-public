"""User-confirmed source speech metadata, independent of running reverse workers."""
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix='/api/reverse-voice')

# 口播保存只接受本机工作台发起的请求。按 host 判定、不看端口（2026-11-01）：
# 原来把允许的 Origin 逐条写成 8789/8791 四个值，工作台一换端口保存就必 403
# （8787 旧实例、8897 临时实例都中招）。现在 127.0.0.1 / localhost 的任意端口都放行。
_LOCAL_HOSTS = frozenset({'127.0.0.1', 'localhost'})


def is_local_origin(origin):
    """Origin 的 host 是否为本机回环地址（端口不参与判定）。"""
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def normalize(data):
    if not isinstance(data, dict) or data.get('state') not in ('unknown', 'spoken', 'none'):
        raise ValueError('请选择原片口播状态')
    language = data.get('language', '')
    if not isinstance(language, str) or len(language) > 100:
        raise ValueError('语言须为 100 字以内文本')
    language = language.strip()
    if data['state'] == 'spoken' and not language:
        raise ValueError('有口播时请指定语言')
    return {'state': data['state'], 'language': language if data['state'] == 'spoken' else '',
            'source': 'user_confirmed' if data['state'] != 'unknown' else 'unknown'}


def read_directory(directory):
    path = Path(directory) / 'voice.json'
    if path.resolve().parent != Path(directory).resolve():
        raise ValueError('口播信息路径不合法')
    if not path.exists():
        return normalize({'state': 'unknown'})
    if path.stat().st_size > 8192:
        raise ValueError('口播信息文件过大，请重新确认')
    data = json.loads(path.read_text(encoding='utf-8'))
    return {**normalize(data), 'updated_at': data.get('updated_at')}


def task_directory(vid):
    import tk_reverse as T
    try:
        directory = T.task_dir(vid)
    except T.ReverseError as exc:
        raise HTTPException(400, str(exc)) from None
    if not (directory / 'status.json').is_file():
        raise HTTPException(404, '反推任务不存在')
    return directory


@router.get('/profile')
def profile():
    return {'feature': 'reverse_voice', 'version': 1}


@router.get('/{vid}')
def read(vid: str):
    directory = task_directory(vid)
    try:
        return {'ok': True, 'voice': read_directory(directory)}
    except (ValueError, OSError):
        raise HTTPException(409, '原片口播信息无法读取，请在反推页重新确认并保存') from None


@router.post('/{vid}')
def save(vid: str, payload: dict, request: Request):
    origin = request.headers.get('origin')
    if origin and not is_local_origin(origin):
        raise HTTPException(403, '仅允许本机工作台保存口播信息')
    directory = task_directory(vid)
    try:
        data = {**normalize(payload), 'updated_at': time.time()}
        target = directory / 'voice.json'
        if target.resolve().parent != directory.resolve():
            raise ValueError('口播信息路径不合法')
        fd, temp = tempfile.mkstemp(prefix='.voice-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            import tk_reverse as T
            T._replace_retry(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
        return {'ok': True, 'voice': data}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(500, '口播信息保存失败，请重试；未修改反推报告或任务状态') from None
