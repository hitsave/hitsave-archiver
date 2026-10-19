from press_material_description import press_material_description


def test_scrubs_inventory_images_only() -> None:
    inventory = {
        "files": [
            {"path": "data/1024x500.png", "mime": "image/png"},
            {"path": "data/2_mat.jpg", "mime": "image/jpeg"},
        ]
    }
    text = press_material_description("Scrubs", inventory)
    assert text == "Press material for Scrubs."


def test_mixed_inventory() -> None:
    inventory = {
        "files": [
            {"path": "data/a.png", "mime": "image/png"},
            {"path": "data/b.pdf", "mime": "application/pdf"},
            {"path": "data/c.mp4", "mime": "video/mp4"},
        ]
    }
    text = press_material_description("Example Game", inventory)
    assert "images" not in text
    assert "documents" in text
    assert "videos" in text
    assert "MobyGames" not in text


def test_empty_inventory_fallback() -> None:
    assert "digital files from the press kit" in press_material_description("Foo", None)
