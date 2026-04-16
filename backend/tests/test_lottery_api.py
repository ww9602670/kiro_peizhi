from app.api.lottery import (
    _build_current_install_response,
    _default_current_install_response,
)


def test_current_install_response_clamps_negative_countdowns():
    response = _build_current_install_response(
        {
            "installments": "3397193",
            "state": "1",
            "close_countdown_sec": -47,
            "open_countdown_sec": "-9",
            "pre_lottery_result": "1,2,3",
            "pre_installments": "3397192",
            "template_code": "JNDPCDD",
        }
    )

    assert response.installments == "3397193"
    assert response.state == 1
    assert response.close_countdown_sec == 0
    assert response.open_countdown_sec == 0


def test_default_current_install_response_is_frontend_safe():
    response = _default_current_install_response()

    assert response.installments == ""
    assert response.state == 0
    assert response.close_countdown_sec == 0
    assert response.open_countdown_sec == 0
    assert response.pre_lottery_result == ""
    assert response.pre_installments == ""
    assert response.template_code == ""
