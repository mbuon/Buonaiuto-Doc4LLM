"""Tests for _detect_locale() heuristic in service.py."""

from buonaiuto_doc4llm.service import _detect_locale


# ---------------------------------------------------------------------------
# German
# ---------------------------------------------------------------------------

def test_german_stripe_webhook_content() -> None:
    """Realistic German Stripe doc excerpt must be detected as 'de'."""
    content = (
        "# Registrieren Sie Stripe-Ereignisse in Ihrem Webhook-Endpoint\n\n"
        "Überwachen Sie Ereignisse von Stripe auf Ihrem Webhook-Endpoint, "
        "damit Ihre Integration automatisch Reaktionen auslösen kann.\n\n"
        "Stripe kann Ereignisdaten an einen Webhook-Endpoint in Ihrem Konto "
        "senden, wenn Ereignisse in Ihrem Stripe-Konto erstellen werden. "
        "Sie können einen Webhook-Endpoint über die API oder das Dashboard "
        "registrieren und konfigurieren.\n"
    )
    assert _detect_locale(content) == "de"


def test_german_short_markers() -> None:
    """A document heavy on short German connectives should still be detected."""
    content = (
        "Dies ist eine einfache Anleitung. "
        "Sie können die Funktion erstellen und konfigurieren. "
        "Die Zahlung wird nicht automatisch verarbeitet."
    )
    assert _detect_locale(content) == "de"


# ---------------------------------------------------------------------------
# French
# ---------------------------------------------------------------------------

def test_french_content() -> None:
    content = (
        "# Documentation de paiement\n\n"
        "Cette page vous montre comment créer une intégration "
        "avec la configuration de votre événement automatique. "
        "Vous pouvez également ajouter des paramètres pour la requête.\n"
    )
    assert _detect_locale(content) == "fr"


# ---------------------------------------------------------------------------
# Spanish
# ---------------------------------------------------------------------------

def test_spanish_content() -> None:
    content = (
        "# Documentación de integración\n\n"
        "Esta página muestra cómo crear una configuración "
        "para su evento automático. Usted también puede agregar "
        "parámetros para la solicitud.\n"
    )
    assert _detect_locale(content) == "es"


# ---------------------------------------------------------------------------
# English (no false positives)
# ---------------------------------------------------------------------------

def test_english_content() -> None:
    content = (
        "# Stripe Webhooks\n\n"
        "Listen to events on your Stripe account so your integration can "
        "automatically trigger reactions. You can register and configure "
        "webhook endpoints using the API or the Dashboard.\n"
    )
    assert _detect_locale(content) == "en"


def test_english_with_fund_and_under() -> None:
    """Words containing 'und' (fund, under) must not trigger German."""
    content = (
        "The fund manager will refund your payment. "
        "Under certain conditions the underlying system "
        "handles redundant requests. Fundamental changes "
        "are underway.\n"
    ) * 10  # repeat to fill sample
    assert _detect_locale(content) == "en"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_content_defaults_to_english() -> None:
    assert _detect_locale("") == "en"


def test_short_content_defaults_to_english() -> None:
    assert _detect_locale("Hello world") == "en"
