"""Regression tests for background library edits."""

from gui.worker import Worker


def test_clearing_metadata_persists_to_symbol(tmp_path):
    sym_dir = tmp_path / "symbol"
    sym_dir.mkdir()
    sym_file = sym_dir / "test.kicad_sym"
    sym_file.write_text(
        '(kicad_symbol_lib (version 20211014) (generator test)\n'
        '  (symbol "Test"\n'
        '    (property "LCSC" "C123")\n'
        '    (property "Description" "Old description")\n'
        '  )\n)\n',
        encoding="utf-8",
    )

    worker = Worker()
    worker.update_property("C123", "Description", "", str(tmp_path))
    worker.stop()
    worker.run()

    saved = sym_file.read_text(encoding="utf-8")
    assert '(property "Description" ""' in saved
    assert "Old description" not in saved
