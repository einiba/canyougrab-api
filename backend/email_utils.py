"""
Email normalization and disposable email detection.
Used at signup to prevent multi-accounting via Gmail aliases and throwaway domains.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Gmail and Google-hosted domains that support dot-stripping and plus-addressing
GMAIL_DOMAINS = {'gmail.com', 'googlemail.com'}

# Known disposable email domains (curated subset — supplement with API for coverage)
# This list covers the most common disposable services; expand or use DeBounce API for more.
DISPOSABLE_DOMAINS = frozenset({
    # Major disposable email services
    'guerrillamail.com', 'guerrillamail.de', 'guerrillamail.net', 'guerrillamail.org',
    'guerrillamailblock.com', 'grr.la', 'sharklasers.com', 'guerrillamail.info',
    'mailinator.com', 'mailinator2.com', 'mailinator.net',
    'tempmail.com', 'temp-mail.org', 'temp-mail.io',
    'throwaway.email', 'throwaway.com',
    'yopmail.com', 'yopmail.fr', 'yopmail.net',
    'mailnesia.com', 'mailnull.com',
    'dispostable.com', 'disposableemailaddresses.emailmiser.com',
    'trashmail.com', 'trashmail.me', 'trashmail.net', 'trashmail.org',
    'tempinbox.com', 'tempinbox.xyz',
    'fakeinbox.com', 'fakemail.net',
    'getnada.com', 'nada.email',
    'maildrop.cc', 'mailcatch.com',
    'discard.email', 'discardmail.com', 'discardmail.de',
    'harakirimail.com',
    'mailexpire.com',
    'mohmal.com',
    'burnermail.io',
    'spamgourmet.com',
    'mytemp.email',
    'tempail.com',
    'emailondeck.com',
    'mintemail.com',
    'tempr.email',
    'inboxalias.com',
    'crazymailing.com',
    'mailsac.com',
    'tmail.ws',
    '10minutemail.com', '10minutemail.net',
    'minutemail.com',
    'emailfake.com',
    'generator.email',
    'guerrillamail.biz',
    'mailtemp.net',
    'tempmailo.com',
    'mailnator.com',
    'tempmailaddress.com',
    'getairmail.com',
    'meltmail.com',
    'spamfree24.org',
    'jetable.org',
    'maildax.com',
    'trash-mail.com',
})


def normalize_email(email: str) -> str:
    """Normalize an email address for deduplication.

    - Lowercases everything
    - For Gmail: strips dots from local part, removes +suffix
    - For other providers: removes +suffix only
    - Normalizes googlemail.com → gmail.com

    Returns the normalized form. Store alongside the original for communication.
    """
    email = email.strip().lower()
    if '@' not in email:
        return email

    local, domain = email.rsplit('@', 1)

    # Normalize googlemail.com to gmail.com
    if domain == 'googlemail.com':
        domain = 'gmail.com'

    # Strip +suffix (works for Gmail, Outlook, FastMail, etc.)
    if '+' in local:
        local = local.split('+', 1)[0]

    # Gmail-specific: dots in local part are ignored
    if domain in GMAIL_DOMAINS:
        local = local.replace('.', '')

    return f'{local}@{domain}'


def is_disposable_email(email: str) -> bool:
    """Check if an email uses a known disposable/temporary domain."""
    email = email.strip().lower()
    if '@' not in email:
        return False
    domain = email.rsplit('@', 1)[1]
    return domain in DISPOSABLE_DOMAINS


def validate_signup_email(email: str) -> dict:
    """Validate an email for signup. Returns dict with status and normalized form.

    Returns:
        {
            'valid': bool,
            'normalized': str,
            'reason': str | None,  # rejection reason if not valid
        }
    """
    email = email.strip().lower()

    if not email or '@' not in email:
        return {'valid': False, 'normalized': email, 'reason': 'Invalid email format'}

    local, domain = email.rsplit('@', 1)

    if not local or not domain or '.' not in domain:
        return {'valid': False, 'normalized': email, 'reason': 'Invalid email format'}

    if is_disposable_email(email):
        return {'valid': False, 'normalized': normalize_email(email), 'reason': 'Disposable email addresses are not allowed'}

    return {'valid': True, 'normalized': normalize_email(email), 'reason': None}
