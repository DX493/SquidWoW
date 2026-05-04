#!/usr/bin/env python3
"""
Remove MCLQ — supprime tous les chunks MCLQ des ADTs vanilla
et nettoie les flags MCNK associés.
Pas de MH2O généré — l'eau sera absente mais Noggit ne crashera pas.

Usage:
    python remove_mclq.py input.adt [output.adt]
    python remove_mclq.py input.adt   # in-place avec backup .bak
"""

import sys, struct, shutil
from pathlib import Path

def ru32(d, o): return struct.unpack_from('<I', d, o)[0]
def wu32(b, o, v): struct.pack_into('<I', b, o, v)

def make_chunk(magic, data):
    return magic[::-1].encode('latin-1') + struct.pack('<I', len(data)) + bytes(data)

def iter_chunks(data, start=0, end=None):
    if end is None: end = len(data)
    pos = start
    while pos + 8 <= end:
        magic = data[pos:pos+4][::-1].decode('latin-1')
        size  = ru32(data, pos+4)
        if pos + 8 + size > end: break
        yield pos, magic, size
        pos += 8 + size

MCNK_LIQUID_FLAGS = 0x0004 | 0x0008 | 0x0010 | 0x0020
MCNK_FLAG_0x8000  = 0x8000

def strip_mcnk(data, chunk_off, chunk_sz):
    hdr     = chunk_off + 8
    buf     = bytearray(data[chunk_off : chunk_off + 8 + chunk_sz])
    hdr_rel = 8

    # Nettoyer flags
    flags     = ru32(buf, hdr_rel)
    new_flags = (flags & ~MCNK_LIQUID_FLAGS) | MCNK_FLAG_0x8000
    wu32(buf, hdr_rel, new_flags)

    # Lire ofsLiquid / sizeLiquid
    ofs_liq  = ru32(buf, hdr_rel + 0x60)
    siz_liq  = ru32(buf, hdr_rel + 0x64)

    # Effacer les champs
    wu32(buf, hdr_rel + 0x60, 0)
    wu32(buf, hdr_rel + 0x64, 0)

    # Tronquer le chunk avant le MCLQ
    if ofs_liq > 0 and siz_liq > 0:
        end_data = ofs_liq   # relatif à chunk_off
        new_data = buf[:8 + end_data]
        # Garder ce qui suit le MCLQ (ex: MCSE)
        after_mclq = ofs_liq + siz_liq
        if after_mclq < chunk_sz:
            new_data += buf[8 + after_mclq : 8 + chunk_sz]
        wu32(new_data, 4, len(new_data) - 8)
        return bytes(new_data)

    return bytes(buf)

def convert(input_path, output_path):
    src = Path(input_path)
    if not src.exists():
        print(f"ERREUR : {src}"); return False

    data = src.read_bytes()
    top  = list(iter_chunks(data))
    mcnk_list = [(o,s) for o,n,s in top if n=='MCNK']

    if not mcnk_list:
        print(f"ERREUR : aucun MCNK dans {src}"); return False

    # Traiter chaque MCNK
    new_mcnks  = [strip_mcnk(data, o, s) for o, s in mcnk_list]
    mclq_count = sum(1 for o,s in mcnk_list
                     if ru32(data, o+8+0x60) > 0 and ru32(data, o+8+0x64) > 0)

    # Reconstruire MCIN
    first_off  = mcnk_list[0][0]
    prefix     = bytearray(data[:first_off])
    mcin_info  = next(((o,s) for o,n,s in top if n=='MCIN'), None)
    if mcin_info:
        mo, ms = mcin_info
        new_mcin = bytearray(data[mo+8 : mo+8+ms])
        pos = first_off
        for i, blob in enumerate(new_mcnks):
            struct.pack_into('<I', new_mcin, i*16+0, pos)
            struct.pack_into('<I', new_mcin, i*16+4, len(blob))
            pos += len(blob)
        prefix[mo+8 : mo+8+ms] = new_mcin

    output = bytearray(prefix)
    for blob in new_mcnks:
        output += blob

    Path(output_path).write_bytes(output)
    print(f"{src.name} → {mclq_count} MCLQ supprimés  ({len(data):,}→{len(output):,} bytes)")
    return True

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) >= 3 else inp
    if inp == out:
        shutil.copy2(inp, inp + '.bak')
    sys.exit(0 if convert(inp, out) else 1)
