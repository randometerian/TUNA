"""TUNA metadata extraction via mutagen"""
import hashlib
from pathlib import Path
from tuna.config import CACHE_DIR


def read_metadata(file_path: str) -> "Track":
    """Read ID3 / Vorbis / MP4 tags and extract embedded cover art."""
    from tuna.playlist import Track

    track = Track(path=file_path)
    p     = Path(file_path)
    track.title = p.stem   # filename fallback

    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return track

        track.title  = str(audio.get("title",  [p.stem])[0])
        track.artist = str(audio.get("artist", ["Unknown Artist"])[0])
        track.album  = str(audio.get("album",  [""])[0])

        if audio.info:
            track.duration = audio.info.length

        cover_data = _extract_cover(file_path)
        if cover_data:
            h = hashlib.md5(file_path.encode()).hexdigest()
            cover_path = CACHE_DIR / f"{h}.jpg"
            if not cover_path.exists():
                with open(cover_path, "wb") as f:
                    f.write(cover_data)
            track.cover_path = str(cover_path)

    except Exception:
        pass

    return track


def _extract_cover(file_path: str) -> bytes | None:
    """Return raw cover image bytes from any common audio container."""
    # MP3 — ID3 APIC frame
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import APIC
        audio = MP3(file_path)
        for tag in audio.tags.values():
            if isinstance(tag, APIC):
                return tag.data
    except Exception:
        pass

    # FLAC — embedded picture block
    try:
        from mutagen.flac import FLAC
        audio = FLAC(file_path)
        if audio.pictures:
            return audio.pictures[0].data
    except Exception:
        pass

    # M4A / AAC — covr atom
    try:
        from mutagen.mp4 import MP4
        audio  = MP4(file_path)
        covers = audio.tags.get("covr", [])
        if covers:
            return bytes(covers[0])
    except Exception:
        pass

    # OGG Vorbis — metadata_block_picture (base64-encoded FLAC picture block)
    try:
        from mutagen.oggvorbis import OggVorbis
        import base64, struct
        audio = OggVorbis(file_path)
        for block in audio.get("metadata_block_picture", []):
            data   = base64.b64decode(block)
            offset = 8
            mime_len = struct.unpack(">I", data[offset:offset+4])[0]
            offset  += 4 + mime_len
            desc_len = struct.unpack(">I", data[offset:offset+4])[0]
            offset  += 4 + desc_len + 16 + 4
            img_len  = struct.unpack(">I", data[offset:offset+4])[0]
            return data[offset+4 : offset+4+img_len]
    except Exception:
        pass

    return None


def format_duration(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
