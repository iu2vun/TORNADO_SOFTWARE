

def find_data(combo, value) -> int:
    """Indice dell'elemento con itemData == value (findData non confronta in modo affidabile le tuple)."""
    for k in range(combo.count()):
        if combo.itemData(k) == value:
            return k
    return -1
