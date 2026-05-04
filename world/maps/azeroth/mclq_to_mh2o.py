#!/usr/bin/env python3
"""
Convertisseur MCLQ → MH2O
Vanilla 1.12 → WotLK 3.3.5

Pour chaque MCNK contenant un sous-chunk MCLQ :
  1. Lit les données MCLQ (vertices, render flags)
  2. Construit les entrées MH2O correspondantes
  3. Supprime le MCLQ du MCNK
  4. Nettoie les liquid flags du MCNK
  5. Injecte le chunk MH2O au niveau top-level

Usage:
    python mclq_to_mh2o.py input.adt [output.adt]
    python mclq_to_mh2o.py input.adt          # in-place avec backup .bak
"""

import sys, struct, shutil
from pathlib import Path

# ─── Helpers ─────────────────────────────────────────────────────────────────

def ru32(d, o): return struct.unpack_from('<I', d, o)[0]
def ru16(d, o): return struct.unpack_from('<H', d, o)[0]
def ru8 (d, o): return struct.unpack_from('<B', d, o)[0]
def rf32(d, o): return struct.unpack_from('<f', d, o)[0]
def wu32(b, o, v): struct.pack_into('<I', b, o, v)
def wu16(b, o, v): struct.pack_into('<H', b, o, v)

def make_chunk(magic, data_bytes):
    """Crée un chunk avec son header magic+size."""
    return magic[::-1].encode('latin-1') + struct.pack('<I', len(data_bytes)) + bytes(data_bytes)

def iter_chunks(data, start=0, end=None):
    if end is None: end = len(data)
    pos = start
    while pos + 8 <= end:
        magic = data[pos:pos+4][::-1].decode('latin-1')
        size  = ru32(data, pos+4)
        if pos + 8 + size > end: break
        yield pos, magic, size
        pos += 8 + size

# ─── Mapping MCLQ → LiquidType WotLK ────────────────────────────────────────
# MCNK flags liquid bits :
#   0x08 = ocean
#   0x10 = magma
#   0x20 = slime
#   sinon = eau douce (river/lake)
#
# LiquidType.dbc IDs WotLK 3.3.5 :
#   1 = Liquid_River  (eau douce)
#   2 = Liquid_Ocean  (océan)
#   3 = Liquid_Magma  (magma)
#   4 = Liquid_Slime  (slime)

MCNK_LIQUID_FLAGS = 0x0004 | 0x0008 | 0x0010 | 0x0020
MCNK_FLAG_0x8000  = 0x8000

def get_liquid_type(mcnk_flags):
    if mcnk_flags & 0x0010: return 3   # magma
    if mcnk_flags & 0x0020: return 4   # slime
    if mcnk_flags & 0x0008: return 2   # ocean
    return 1                            # eau douce

# ─── Lecture MCLQ ─────────────────────────────────────────────────────────────

VERTEX_SIZE = 8   # uint8×4 + float = 8 bytes
GRID_VERTS  = 9 * 9   # 81 vertices
GRID_CELLS  = 8 * 8   # 64 cellules (render flags)
FLOW_SIZE   = 42      # 2 flow structs × 42 bytes = 84 bytes

def parse_mclq(data, mclq_data_off, mclq_size):
    """
    Parse un chunk MCLQ vanilla.
    Retourne un dict avec les données décodées.
    """
    base = mclq_data_off
    min_h = rf32(data, base + 0)
    max_h = rf32(data, base + 4)

    # 81 vertices (9×9)
    vertices = []
    voff = base + 8
    for i in range(GRID_VERTS):
        depth  = ru8(data, voff + 0)
        flow0  = ru8(data, voff + 1)
        flow1  = ru8(data, voff + 2)
        filler = ru8(data, voff + 3)
        height = rf32(data, voff + 4)
        vertices.append((depth, flow0, flow1, filler, height))
        voff += VERTEX_SIZE

    # Render flags (8×8 = 64 bytes)
    flags_off = base + 8 + GRID_VERTS * VERTEX_SIZE
    render_flags = list(data[flags_off : flags_off + GRID_CELLS])

    return {
        'min_h':        min_h,
        'max_h':        max_h,
        'vertices':     vertices,
        'render_flags': render_flags,
    }

# ─── Construction MH2O ────────────────────────────────────────────────────────
#
# Format MH2O WotLK 3.3.5 :
#
# Chunk header : magic + size (géré par make_chunk)
#
# [3072 bytes] SChunkHeader[256] — une entrée par MCNK (row-major)
#   uint32 ofsData  → offset depuis le début des données MH2O vers SMH2OChunk
#   uint32 layerCount → nombre de layers (0 = pas d'eau)
#   uint32 ofsExtraData → offset vers bitmask de rendu (ou 0)
#
# Suivi de blocs variables pour chaque MCNK avec eau :
#   SMH2OChunk :
#     uint16 liquidType
#     uint16 liquidVertexFormat (0 = height+depth)
#     float  minHeightLevel
#     float  maxHeightLevel
#     uint8  xOffset (0)
#     uint8  yOffset (0)
#     uint8  width   (8)
#     uint8  height  (8)
#     uint32 ofsInfoMask   → offset vers le bitmask 8×8 bits (8 bytes)
#     uint32 ofsVertexData → offset vers les heights (float[9×9])
#     uint32 nVertices     (81)
#
#   Bitmask [8 bytes] = 1 bit par cellule (row-major, LSB first)
#   VertexData [81 × 4 bytes] = float heights
#

SMH2O_CHUNK_HEADER_SIZE = 2+2+4+4+1+1+1+1+4+4+4  # 24 bytes
SH2O_HEADER_ENTRY_SIZE  = 12                        # 3 × uint32
SH2O_HEADERS_TOTAL      = 256 * SH2O_HEADER_ENTRY_SIZE  # 3072 bytes

def build_mh2o(mclq_map):
    """
    Construit le contenu du chunk MH2O (format WotLK 3.3.5 confirmé).
    Structure par chunk d'eau :
      SChunkHeader (12B) : ofsData, layerCount, ofsExtraData
      SMH2OChunk   (24B) : liquidType, flags, minH, maxH, xo, yo, w, h, ofsMask, ofsVerts
      InfoMask     (?B)  : ceil(w*h/8) bytes
      ExtraData    (16B) : depth/attributs
      VertexData   (?B)  : (w+1)*(h+1) floats heights
    """
    headers = bytearray(256 * 12)   # 3072 bytes
    vardata = bytearray()

    for iy in range(16):
        for ix in range(16):
            idx  = iy * 16 + ix
            hoff = idx * 12
            key  = (ix, iy)
            if key not in mclq_map:
                continue

            entry     = mclq_map[key]
            liq_type  = entry['liquid_type']
            min_h     = entry['min_h']
            max_h     = entry['max_h']
            vertices  = entry['vertices']   # 9×9 = 81 vertices
            rflags    = entry['render_flags']  # 8×8 = 64 bytes

            # ── Calculer la bounding box réelle (xo, yo, w, h) ──────────────
            # Trouver les cellules actives depuis le render_flags
            # render_flag bit 0 = cellule visible/active
            active_cols = set()
            active_rows = set()
            for cell in range(64):
                row, col = divmod(cell, 8)
                if rflags[cell] & 0x01:
                    active_rows.add(row)
                    active_cols.add(col)

            if not active_rows:
                # Aucune cellule active — utiliser toute la grille
                xo, yo, w, h = 0, 0, 8, 8
            else:
                xo = min(active_cols)
                yo = min(active_rows)
                w  = max(active_cols) - xo + 1
                h  = max(active_rows) - yo + 1

            # ── Bitmask : 1 bit par cellule dans la bbox (w×h) ───────────────
            n_cells   = w * h
            mask_bytes = (n_cells + 7) // 8
            bitmask    = bytearray(mask_bytes)
            for row in range(h):
                for col in range(w):
                    global_cell = (yo + row) * 8 + (xo + col)
                    if rflags[global_cell] & 0x01:
                        bit_idx = row * w + col
                        bitmask[bit_idx // 8] |= (1 << (bit_idx % 8))

            # ── Extra data (16 bytes) : depth par cellule ────────────────────
            # Stocker les valeurs de depth des cellules de la bbox
            extra = bytearray(16)
            cell_idx = 0
            for row in range(h):
                for col in range(w):
                    if cell_idx >= 16: break
                    global_cell = (yo + row) * 8 + (xo + col)
                    v_idx = (yo + row) * 9 + (xo + col)
                    depth_val = vertices[v_idx][0] if v_idx < len(vertices) else 0
                    extra[cell_idx] = min(255, depth_val)
                    cell_idx += 1

            # ── Vertex heights : (w+1)×(h+1) floats ─────────────────────────
            n_verts  = (w+1) * (h+1)
            heights  = bytearray()
            real_min = float('inf')
            real_max = float('-inf')
            for row in range(h+1):
                for col in range(w+1):
                    v_idx = (yo + row) * 9 + (xo + col)
                    if v_idx < len(vertices):
                        hval = vertices[v_idx][4]
                    else:
                        hval = min_h
                    heights += struct.pack('<f', hval)
                    if hval != 0.0:
                        real_min = min(real_min, hval)
                        real_max = max(real_max, hval)

            if real_min == float('inf'):
                real_min = min_h
                real_max = max_h

            # ── Offsets dans vardata ──────────────────────────────────────────
            current = len(vardata)
            smh2o_off  = SH2O_HEADERS_TOTAL + current
            extra_off  = smh2o_off + SMH2O_CHUNK_HEADER_SIZE
            mask_off   = extra_off + 16
            verts_off  = mask_off + mask_bytes

            # ── SMH2OChunk (24 bytes) ─────────────────────────────────────────
            smh2o = bytearray(SMH2O_CHUNK_HEADER_SIZE)
            wu16(smh2o, 0,  liq_type)      # liquidType
            wu16(smh2o, 2,  0x0002)        # flags (0x2 = has vertex data)
            struct.pack_into('<f', smh2o, 4,  real_min)
            struct.pack_into('<f', smh2o, 8,  real_max)
            smh2o[12] = xo
            smh2o[13] = yo
            smh2o[14] = w
            smh2o[15] = h
            wu32(smh2o, 16, mask_off if mask_bytes > 0 else 0)   # ofsInfoMask
            wu32(smh2o, 20, verts_off)                            # ofsVertexData

            vardata += smh2o
            vardata += extra      # 16 bytes extra data
            vardata += bitmask    # mask_bytes bytes
            vardata += heights    # n_verts floats

            # ── SChunkHeader ──────────────────────────────────────────────────
            wu32(headers, hoff + 0, smh2o_off)   # ofsData
            wu32(headers, hoff + 4, 1)            # layerCount
            wu32(headers, hoff + 8, extra_off)    # ofsExtraData

    return bytes(headers) + bytes(vardata)


# ─── Reconstruction MCNK ──────────────────────────────────────────────────────

def rebuild_mcnk_without_mclq(data, chunk_off, chunk_sz):
    """
    Retourne les données du MCNK sans le sous-chunk MCLQ,
    et avec les liquid flags du header nettoyés.
    Retourne aussi les données MCLQ lues si présentes.
    """
    hdr       = chunk_off + 8
    flags     = ru32(data, hdr + 0x00)
    ofs_liq   = ru32(data, hdr + 0x60)   # ofsLiquid (relatif à chunk_off)
    size_liq  = ru32(data, hdr + 0x64)   # sizeLiquid (incl. magic+size=8)

    mclq_info = None

    # Lire et supprimer le MCLQ si présent
    if ofs_liq > 0 and size_liq > 8:
        mclq_abs = chunk_off + ofs_liq
        if mclq_abs + 8 <= len(data):
            mclq_magic = data[mclq_abs:mclq_abs+4][::-1].decode('latin-1')
            mclq_dsz   = size_liq - 8   # taille données seules
            if mclq_magic == 'MCLQ' and mclq_dsz >= 720:
                mclq_data_off = mclq_abs + 8
                mclq_info = parse_mclq(data, mclq_data_off, mclq_dsz)

    # Construire le nouveau MCNK sans MCLQ
    buf = bytearray(data[chunk_off : chunk_off + 8 + chunk_sz])
    hdr_rel = 8   # offset du header dans buf

    # Nettoyer les flags liquid et mettre à jour 0x8000
    new_flags = (flags & ~MCNK_LIQUID_FLAGS) | MCNK_FLAG_0x8000
    wu32(buf, hdr_rel + 0x00, new_flags)

    # Effacer ofsLiquid et sizeLiquid
    wu32(buf, hdr_rel + 0x60, 0)
    wu32(buf, hdr_rel + 0x64, 0)

    # Tronquer le chunk avant le MCLQ si présent
    if ofs_liq > 0 and size_liq > 8:
        # Le MCNK data se termine juste avant le MCLQ
        # On garde tout ce qui précède le MCLQ
        end_before_mclq = ofs_liq   # relatif au chunk_off
        new_chunk_data = buf[:8 + end_before_mclq]

        # Chercher MCSE (son emitters) éventuellement présent après MCLQ
        mclq_end = ofs_liq + size_liq
        if mclq_end < chunk_sz:
            # Données après MCLQ (typiquement MCSE)
            after = buf[8 + mclq_end : 8 + chunk_sz]
            new_chunk_data += after

        # Mettre à jour la taille dans le header du chunk
        new_sz = len(new_chunk_data) - 8
        struct.pack_into('<I', new_chunk_data, 4, new_sz)
        return bytes(new_chunk_data), mclq_info

    return bytes(buf), mclq_info

# ─── Convertisseur principal ──────────────────────────────────────────────────

def convert(input_path, output_path):
    src = Path(input_path)
    if not src.exists():
        print(f"ERREUR : fichier introuvable : {src}")
        return False

    print(f"Lecture : {src}  ({src.stat().st_size:,} bytes)")
    data = src.read_bytes()

    top_chunks = list(iter_chunks(data))
    mcnk_list  = [(o, s) for o, n, s in top_chunks if n == 'MCNK']
    chunk_names = [n for _, n, _ in top_chunks]

    if not mcnk_list:
        print("ERREUR : aucun MCNK trouvé")
        return False

    # Vérifier qu'il n'y a pas déjà un MH2O
    if 'MH2O' in chunk_names:
        print("INFO : MH2O déjà présent — conversion ignorée pour ce fichier")
        return True

    print(f"{len(mcnk_list)} MCNKs trouvés")

    # ── Traitement de chaque MCNK ─────────────────────────────────────────────
    mclq_map    = {}
    new_mcnks   = []
    mclq_count  = 0

    for off, sz in mcnk_list:
        hdr = off + 8
        ix  = ru32(data, hdr + 0x04)
        iy  = ru32(data, hdr + 0x08)

        new_mcnk, mclq_info = rebuild_mcnk_without_mclq(data, off, sz)
        new_mcnks.append(new_mcnk)

        if mclq_info is not None:
            mclq_count += 1
            liq_type = get_liquid_type(ru32(data, hdr + 0x00))
            mclq_info['liquid_type'] = liq_type
            mclq_map[(ix, iy)] = mclq_info

    print(f"MCNKs avec MCLQ convertis : {mclq_count}")

    # ── Construire MH2O ───────────────────────────────────────────────────────
    mh2o_data  = build_mh2o(mclq_map)
    mh2o_chunk = make_chunk('MH2O', mh2o_data)
    print(f"MH2O généré : {len(mh2o_chunk):,} bytes")

    # ── Reconstruire MCIN ─────────────────────────────────────────────────────
    first_mcnk_off = mcnk_list[0][0]
    prefix         = bytearray(data[:first_mcnk_off])

    # Localiser MCIN et mettre à jour les offsets
    mcin_info = next(((o, s) for o, n, s in top_chunks if n == 'MCIN'), None)
    if mcin_info:
        mcin_off, mcin_sz = mcin_info
        new_mcin = bytearray(data[mcin_off + 8 : mcin_off + 8 + mcin_sz])
        pos = first_mcnk_off
        for i, blob in enumerate(new_mcnks):
            struct.pack_into('<I', new_mcin, i * 16 + 0, pos)
            struct.pack_into('<I', new_mcin, i * 16 + 4, len(blob))
            pos += len(blob)
        prefix[mcin_off + 8 : mcin_off + 8 + mcin_sz] = new_mcin

    # ── Mise à jour MHDR ──────────────────────────────────────────────────────
    # MHDR contient des offsets relatifs vers MH2O et autres chunks top-level
    # offset = position depuis fin du MHDR chunk (après 0x14 bytes)
    mhdr_info = next(((o, s) for o, n, s in top_chunks if n == 'MHDR'), None)
    if mhdr_info:
        mhdr_off, mhdr_sz = mhdr_info
        mhdr_data_start = mhdr_off + 8

        # Calculer la position de MH2O dans le nouveau fichier
        # Le MH2O sera inséré juste avant les MCNKs
        # Position dans le nouveau fichier = first_mcnk_off (on va insérer MH2O avant)
        # Mais d'abord, recalculons ce que sera first_mcnk_off avec le MH2O inséré
        new_first_mcnk = first_mcnk_off + len(mh2o_chunk)
        mh2o_pos       = first_mcnk_off

        # MHDR offsets sont relatifs au début du fichier ADT + 0x14 bytes (après MVER + MHDR header + MHDR data start)
        # En pratique, ofssMH2O dans MHDR est relatif au début du fichier (offset 0x14 = après MVER+MHDR headers)
        # ofssMH2O est à MHDR+0x20 (offset 32 dans les données MHDR)
        # Valeur = position MH2O - 0x14
        mh2o_mhdr_val = mh2o_pos - 0x14
        struct.pack_into('<I', prefix, mhdr_data_start + 0x20, mh2o_mhdr_val)

    # ── Assembler le fichier final ────────────────────────────────────────────
    output = bytearray(prefix)
    output += mh2o_chunk   # MH2O inséré avant les MCNKs
    for blob in new_mcnks:
        output += blob

    # ── Stats ─────────────────────────────────────────────────────────────────
    print(f"\n{'='*52}")
    print(f"Taille source  : {len(data):>12,} bytes")
    print(f"Taille finale  : {len(output):>12,} bytes")
    print(f"Liquid types   : { {v['liquid_type'] for v in mclq_map.values()} }")

    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(output)
    print(f"\nFichier écrit  : {dst}")
    return True

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) >= 3 else inp

    if inp == out:
        bak = inp + '.bak'
        print(f"Backup : {bak}")
        shutil.copy2(inp, bak)

    ok = convert(inp, out)
    sys.exit(0 if ok else 1)
