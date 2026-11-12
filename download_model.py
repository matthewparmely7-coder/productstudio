"""Downloads the IS-Net background-removal model into the models folder.

Usage: python download_model.py
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

URL = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx'
SHA256 = '60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a'
TARGET = Path(__file__).resolve().parent / 'models' / 'isnet-general-use.onnx'


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    if TARGET.exists() and checksum(TARGET) == SHA256:
        print(f'The model is already in place: {TARGET}')
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    part = TARGET.with_name(TARGET.name + '.part')
    print(f'Downloading the model (178 MB) from\n  {URL}')
    try:
        with urllib.request.urlopen(URL, timeout=60) as reply, open(part, 'wb') as handle:
            total = int(reply.headers.get('Content-Length') or 0)
            done = 0
            while True:
                block = reply.read(1 << 20)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if total:
                    print(f'\r  {done * 100 // total:3d}%  {done >> 20} of {total >> 20} MB', end='', flush=True)
        print()
    except (OSError, ValueError) as error:
        part.unlink(missing_ok=True)
        print(f'\nThe download failed: {error}\n'
              f'You can download the file in a browser instead and save it as\n  {TARGET}')
        return 1
    if checksum(part) != SHA256:
        part.unlink(missing_ok=True)
        print('The downloaded file is damaged (checksum mismatch). Please run this again.')
        return 1
    part.replace(TARGET)
    print(f'Saved to {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
