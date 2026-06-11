import re


REMOTE_ACCESS_SOFTWARE = [
    'anydesk',
    'teamviewer',
    'rustdesk',
    'supremo',
    'ultravnc',
    'realvnc',
    'tightvnc',
    'vnc',
    'chrome remote desktop',
    'remote desktop manager',
    'logmein',
    'splashtop',
    'zoho assist',
]

ADMIN_NETWORK_SOFTWARE = [
    'winscp',
    'putty',
    'wireshark',
    'nmap',
    'advanced ip scanner',
    'angry ip scanner',
    'filezilla',
    'mobaxterm',
    'bitvise ssh client',
    'openvpn',
    'cisco secure client',
    'anyconnect',
    'forticlient',
]

SECURITY_SOFTWARE = [
    'bitdefender',
    'windows defender',
    'microsoft defender',
    'eset',
    'kaspersky',
    'sophos',
    'crowdstrike',
    'sentinelone',
    'malwarebytes',
]

BROWSER_SOFTWARE = [
    'google chrome',
    'microsoft edge',
    'mozilla firefox',
    'opera',
    'brave',
]

OFFICE_SOFTWARE = [
    'microsoft office',
    'microsoft 365',
    'libreoffice',
    'adobe acrobat',
]

UTILITY_SOFTWARE = [
    '7-zip',
    'winrar',
    'powertoys',
    'flameshot',
]

DEVELOPMENT_SOFTWARE = [
    'visual studio code',
    'git',
    'python',
    'node.js',
    'docker',
    'postman',
]

CATEGORY_LABELS = {
    'remote_access': 'Acesso remoto',
    'admin_network': 'Admin/Rede',
    'security': 'Segurança',
    'browser': 'Navegador',
    'office': 'Office',
    'utility': 'Utilitário',
    'development': 'Desenvolvimento',
    'unknown': 'Desconhecido',
}

RISK_LABELS = {
    'security': 'Security',
    'warning': 'Warning',
    'info': 'Info',
    'ok': 'OK',
    'unknown': 'Unknown',
}


def normalize_key(value):
    value = str(value or '').lower().strip()
    value = re.sub(r'[^a-z0-9]+', '_', value)
    return value.strip('_') or 'unknown'


def software_text(software):
    return ' '.join([
        str((software or {}).get('name') or ''),
        str((software or {}).get('publisher') or ''),
        str((software or {}).get('version') or ''),
    ]).lower()


def matches_any(text, terms):
    return any(term in text for term in terms)


def classify_software(software):
    text = software_text(software)
    if matches_any(text, REMOTE_ACCESS_SOFTWARE):
        category = 'remote_access'
        risk_level = 'security'
    elif matches_any(text, ADMIN_NETWORK_SOFTWARE):
        category = 'admin_network'
        risk_level = 'warning'
    elif matches_any(text, SECURITY_SOFTWARE):
        category = 'security'
        risk_level = 'ok'
    elif matches_any(text, BROWSER_SOFTWARE):
        category = 'browser'
        risk_level = 'info'
    elif matches_any(text, OFFICE_SOFTWARE):
        category = 'office'
        risk_level = 'info'
    elif matches_any(text, UTILITY_SOFTWARE):
        category = 'utility'
        risk_level = 'info'
    elif matches_any(text, DEVELOPMENT_SOFTWARE):
        category = 'development'
        risk_level = 'info'
    else:
        category = 'unknown'
        risk_level = 'unknown'

    return {
        'category': category,
        'category_label': CATEGORY_LABELS[category],
        'risk_level': risk_level,
        'risk_label': RISK_LABELS[risk_level],
        'is_sensitive': category in ('remote_access', 'admin_network'),
    }


def classify_sensitive_software(software):
    category = classify_software(software)['category']
    return category if category in ('remote_access', 'admin_network') else ''
