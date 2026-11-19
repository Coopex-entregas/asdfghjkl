"""Identidades GPS da Supervisao antiga para o cadastro oficial Financeiro.

Snapshot de IDs operacionais com 5 divergencias revistas pelo Master em 19/11/2026.\nNao altera cadastros, exclusoes ou producoes.
Atualizar ao confirmar novos vinculos; novos IDs nao reconhecidos DEVEM
falhar fechados para nao posicionar o cooperado errado.
As entregas/producoes do sistema antigo continuam usando seus IDs originais.
"""
SUPERVISAO_PARA_FINANCEIRO = {
    1: 6,
    3: 69,
    4: 63,
    7: 11,
    8: 129,
    9: 13,
    11: 30,
    13: 23,
    14: 88,
    15: 20,
    16: 75,
    17: 41,
    18: 61,
    19: 74,
    21: 93,
    23: 9,
    24: 27,
    25: 77,
    26: 37,
    28: 92,
    30: 72,
    31: 49,
    34: 29,
    35: 90,
    37: 16,
    38: 66,
    40: 56,
    41: 48,
    44: 58,
    45: 135,
    47: 89,
    49: 78,
    51: 19,
    52: 26,
    54: 47,
    55: 73,
    56: 17,
    60: 96,
    61: 10,
    63: 97,
    64: 70,
    71: 59,
    72: 99,
    73: 7,
    74: 39,
    75: 85,
    76: 80,
    77: 8,
    80: 107,
    82: 32,
    83: 35,
    84: 22,
    85: 55,
    87: 62,
    88: 76,
    90: 14,
    91: 108,
    92: 108,
    93: 109,
    94: 87,
    95: 110,
    96: 100,
    97: 111,
    98: 42,
    99: 112,
    100: 113,
    101: 43,
    102: 114,
    103: 160,
    104: 115,
    105: 116,
    106: 117,
    107: 118,
    108: 119,
    109: 79,
    110: 120,
    111: 121,
    112: 71,
    113: 122,
    114: 123,
    115: 124,
    117: 125,
    118: 126,
    119: 15,
    120: 127,
    121: 162,
    300122: 52,
    300123: 65,
    300124: 163,
    300125: 164,
    300126: 165,
    300127: 166,
    300128: 64,
}

def id_gps_oficial(source_id):
    try:
        return SUPERVISAO_PARA_FINANCEIRO.get(int(source_id))
    except (TypeError,ValueError):
        return None
# Contingencia temporaria de 19/11/2026: somente os cadastros oficiais
# confirmados e ATIVOS na conferencia do Master podem transmitir/aparecer.
# Nao substitui verificacao ao vivo no Financeiro apos migracao.
GPS_IDS_FINANCEIRO_ATIVOS_CONFIRMADOS = frozenset({6,7,8,9,10,11,13,14,16,17,19,22,23,26,27,29,30,32,35,39,41,42,43,47,48,49,52,56,58,59,61,62,63,64,65,66,69,70,71,73,74,76,77,79,80,85,87,90,93,99,100,109,114,115,121,124,125,126,162,163,165,166})

def id_gps_oficial_ativo(source_id):
    oficial_id = id_gps_oficial(source_id)
    if oficial_id not in GPS_IDS_FINANCEIRO_ATIVOS_CONFIRMADOS:
        return None
    return oficial_id
