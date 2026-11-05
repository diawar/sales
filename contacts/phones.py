import phonenumbers
import hashlib
from phonenumbers import PhoneNumberType

def parse_phone(raw):
    """Classifies numbering plan only; does not probe subscriber or messenger."""
    try:
        parsed=phonenumbers.parse(raw,'KZ')
    except phonenumbers.NumberParseException:
        return {'number':raw if len(raw)<=40 else '?'+hashlib.sha256(raw.encode()).hexdigest()[:32],'raw':raw,'kind':'unknown'}
    if not phonenumbers.is_valid_number(parsed):
        return {'number':raw if len(raw)<=40 else '?'+hashlib.sha256(raw.encode()).hexdigest()[:32],'raw':raw,'kind':'unknown'}
    category=phonenumbers.number_type(parsed)
    kind='mobile' if category==PhoneNumberType.MOBILE else 'landline' if category==PhoneNumberType.FIXED_LINE else 'service' if category in (PhoneNumberType.TOLL_FREE,PhoneNumberType.PREMIUM_RATE,PhoneNumberType.SHARED_COST) else 'unknown'
    number=phonenumbers.format_number(parsed,phonenumbers.PhoneNumberFormat.E164)
    if parsed.extension:
        number+=' ext '+parsed.extension
    return {'number':number,'raw':raw,'kind':kind}
