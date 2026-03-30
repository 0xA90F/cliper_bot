#!/usr/bin/env python3
"""
export_cookies.py — Helper untuk generate YOUTUBE_COOKIES env var

Cara pakai:
  1. Export cookies dari browser (lihat panduan di bawah)
  2. Jalankan: python export_cookies.py cookies.txt
  3. Copy output ke Railway Variables sebagai YOUTUBE_COOKIES
"""

import sys
import base64
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nContoh: python export_cookies.py ~/Downloads/cookies.txt")
        sys.exit(1)

    cookie_file = Path(sys.argv[1])
    if not cookie_file.exists():
        print(f"❌ File tidak ditemukan: {cookie_file}")
        sys.exit(1)

    content = cookie_file.read_bytes()
    encoded = base64.b64encode(content).decode("utf-8")

    print("=" * 60)
    print("✅ Berhasil! Copy value di bawah ini ke Railway Variables")
    print("   Variable name: YOUTUBE_COOKIES")
    print("=" * 60)
    print(encoded)
    print("=" * 60)
    print(f"\nUkuran file: {len(content)} bytes")
    print("Panjang base64:", len(encoded), "karakter")


if __name__ == "__main__":
    main()
