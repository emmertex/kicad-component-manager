import sys
import json
import subprocess
import os
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QTextEdit, 
                             QFileDialog, QLabel, QProgressBar, QCheckBox,
                             QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt, QThread, Signal

class Worker(QThread):
    finished = Signal()
    progress = Signal(str)
    
    def __init__(self, part_numbers, output_dir, symbol_lib, include_models, download_pdf):
        super().__init__()
        self.part_numbers = part_numbers
        self.output_dir = output_dir
        self.symbol_lib = symbol_lib
        self.include_models = include_models
        self.download_pdf = download_pdf
        
    def run(self):
        try:
            script_path = Path("lcsc2kicad-GUI/JLC2KiCadLib/JLC2KiCadLib.py")
            self.progress.emit(f"Script path: {script_path}")
            if not script_path.exists():
                self.progress.emit(f"Error: Script not found at {script_path}")
                return
                
            cmd = ["python3", str(script_path)]
            cmd.extend(self.part_numbers)
            cmd.extend(["-dir", self.output_dir])
            cmd.extend(["-symbol_lib", self.symbol_lib])
            
            if self.include_models:
                cmd.extend(["-models", "STEP"])
            
            if self.download_pdf:
                cmd.extend(["--download_pdf"])
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            stdout, stderr = process.communicate()
            if stdout:
                self.progress.emit(f"Output: {stdout}")
            if stderr:
                self.progress.emit(f"Error: {stderr}")
            if process.returncode != 0:
                self.progress.emit(f"Process failed with code {process.returncode}")
                    
            self.finished.emit()
            
        except Exception as e:
            self.progress.emit(f"Error: {str(e)}")
            self.finished.emit()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LCSC to KiCad Library Converter")
        self.setMinimumSize(800, 600)
        
        # Main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        # Part number input section
        input_layout = QHBoxLayout()
        self.part_input = QLineEdit()
        self.part_input.setPlaceholderText("Enter LCSC part number...")
        self.part_input.returnPressed.connect(self.add_part)
        add_button = QPushButton("Add Part")
        add_button.clicked.connect(self.add_part)
        input_layout.addWidget(self.part_input)
        input_layout.addWidget(add_button)
        layout.addLayout(input_layout)
        
        # Parts list and comment section
        parts_layout = QHBoxLayout()
        
        list_and_comment_layout = QVBoxLayout()
        
        self.parts_list = QListWidget()
        self.parts_list.itemSelectionChanged.connect(self.on_selection_changed)
        list_and_comment_layout.addWidget(self.parts_list)
        
        # Comment section
        comment_layout = QHBoxLayout()
        comment_label = QLabel("Comment:")
        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText("Add comment for selected part...")
        self.comment_input.returnPressed.connect(self.update_comment)
        update_comment_btn = QPushButton("Update Comment")
        update_comment_btn.clicked.connect(self.update_comment)
        comment_layout.addWidget(comment_label)
        comment_layout.addWidget(self.comment_input)
        comment_layout.addWidget(update_comment_btn)
        list_and_comment_layout.addLayout(comment_layout)
        
        parts_layout.addLayout(list_and_comment_layout)
        
        # Buttons for list management
        button_layout = QVBoxLayout()
        delete_button = QPushButton("Delete Selected")
        delete_button.clicked.connect(self.delete_selected_part)
        clear_button = QPushButton("Clear All")
        clear_button.clicked.connect(self.clear_parts)
        save_button = QPushButton("Save List")
        save_button.clicked.connect(self.save_parts)
        load_button = QPushButton("Load List")
        load_button.clicked.connect(self.load_parts)
        
        button_layout.addWidget(delete_button)
        button_layout.addWidget(clear_button)
        button_layout.addWidget(save_button)
        button_layout.addWidget(load_button)
        button_layout.addStretch()
        parts_layout.addLayout(button_layout)
        
        layout.addLayout(parts_layout)
        
        # Output directory selection
        dir_layout = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Output directory")
        dir_button = QPushButton("Browse")
        dir_button.clicked.connect(self.browse_directory)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(dir_button)
        layout.addLayout(dir_layout)
        
        # Symbol library name
        symbol_layout = QHBoxLayout()
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("Symbol library name (default: components)")
        self.symbol_input.setText("components")
        self.symbol_input.textChanged.connect(self.save_cache)
        symbol_layout.addWidget(self.symbol_input)
        layout.addLayout(symbol_layout)
        
        # Include 3D models checkbox
        self.models_check = QCheckBox("Include 3D models (STEP)")
        self.models_check.setChecked(True)
        self.models_check.toggled.connect(self.save_cache)
        layout.addWidget(self.models_check)
        
        # Download PDF datasheets checkbox
        self.pdf_check = QCheckBox("Download PDF datasheets")
        self.pdf_check.setChecked(False)
        self.pdf_check.toggled.connect(self.save_cache)
        layout.addWidget(self.pdf_check)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Log output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)
        
        # Convert button
        self.convert_button = QPushButton("Convert")
        self.convert_button.clicked.connect(self.start_conversion)
        layout.addWidget(self.convert_button)
        
        # Load cached settings
        self.load_cache()
    
    def normalize_part_number(self, part):
        """Normalize part number to include 'C' prefix if missing."""
        part = part.strip().upper()
        if part.isdigit():
            return f"C{part}"
        elif part.startswith('C') and part[1:].isdigit():
            return part
        return None

    def create_list_item(self, part_number, comment=""):
        """Create a list item with part number and comment."""
        item = QListWidgetItem(f"{part_number} - {comment}" if comment else part_number)
        item.setData(Qt.UserRole, {"part": part_number, "comment": comment})
        return item

    def add_part(self):
        """Add a new part to the list."""
        part_number = self.normalize_part_number(self.part_input.text())
        if not part_number:
            self.log_message("Please enter a valid LCSC part number")
            return
            
        # Check if part already exists
        existing_parts = [self.parts_list.item(i).data(Qt.UserRole)["part"] 
                         for i in range(self.parts_list.count())]
        if part_number not in existing_parts:
            item = self.create_list_item(part_number)
            self.parts_list.addItem(item)
            self.log_message(f"Added part: {part_number}")
        else:
            self.log_message(f"Part {part_number} already in list")
            
        self.part_input.clear()

    def on_selection_changed(self):
        """Update comment field when selection changes."""
        selected_items = self.parts_list.selectedItems()
        if selected_items:
            item_data = selected_items[0].data(Qt.UserRole)
            self.comment_input.setText(item_data["comment"])
        else:
            self.comment_input.clear()

    def update_comment(self):
        """Update the comment for the selected part."""
        selected_items = self.parts_list.selectedItems()
        if not selected_items:
            self.log_message("Please select a part to update comment")
            return

        item = selected_items[0]
        item_data = item.data(Qt.UserRole)
        new_comment = self.comment_input.text().strip()
        
        item_data["comment"] = new_comment
        item.setData(Qt.UserRole, item_data)
        item.setText(f"{item_data['part']} - {new_comment}" if new_comment else item_data['part'])
        self.log_message(f"Updated comment for {item_data['part']}")
    
    def delete_selected_part(self):
        """Delete the selected part from the list."""
        selected_items = self.parts_list.selectedItems()
        for item in selected_items:
            self.parts_list.takeItem(self.parts_list.row(item))
    
    def clear_parts(self):
        """Clear all parts from the list."""
        self.parts_list.clear()

    def save_parts(self):
        """Save parts list to JSON file."""
        if self.parts_list.count() == 0:
            self.log_message("No parts to save")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Parts List",
            "",
            "JSON Files (*.json)"
        )
        
        if file_path:
            parts_data = []
            for i in range(self.parts_list.count()):
                item_data = self.parts_list.item(i).data(Qt.UserRole)
                parts_data.append(item_data)
                
            try:
                with open(file_path, 'w') as f:
                    json.dump(parts_data, f, indent=2)
                self.log_message(f"Saved parts list to {file_path}")
            except Exception as e:
                self.log_message(f"Error saving file: {str(e)}")

    def load_parts(self):
        """Load parts list from JSON file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Parts List",
            "",
            "JSON Files (*.json)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    parts_data = json.load(f)
                
                self.parts_list.clear()
                for part_data in parts_data:
                    item = self.create_list_item(part_data["part"], part_data["comment"])
                    self.parts_list.addItem(item)
                    
                self.log_message(f"Loaded parts list from {file_path}")
            except Exception as e:
                self.log_message(f"Error loading file: {str(e)}")

    def browse_directory(self):
        """Browse for output directory."""
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.dir_input.setText(directory)
            self.save_cache()
            
    def log_message(self, message):
        """Add a message to the log output."""
        self.log_output.append(message)
        
    def start_conversion(self):
        """Start the conversion process."""
        self.convert_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        
        part_numbers = [self.parts_list.item(i).data(Qt.UserRole)["part"] 
                       for i in range(self.parts_list.count())]
        
        if not part_numbers:
            self.log_message("Error: Please add at least one part number")
            self.convert_button.setEnabled(True)
            self.progress_bar.setRange(0, 1)
            return
            
        output_dir = self.dir_input.text()
        if not output_dir:
            self.log_message("Error: Please select an output directory")
            self.convert_button.setEnabled(True)
            self.progress_bar.setRange(0, 1)
            return
            
        self.worker = Worker(
            part_numbers,
            output_dir,
            self.symbol_input.text(),
            self.models_check.isChecked(),
            self.pdf_check.isChecked()
        )
        # Save cache before starting conversion
        self.save_cache()
        
        self.worker.progress.connect(self.log_message)
        self.worker.finished.connect(self.conversion_finished)
        self.worker.start()
        
    def conversion_finished(self):
        """Handle completion of conversion process."""
        self.convert_button.setEnabled(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.log_message("Conversion completed")
        
    def get_cache_file_path(self):
        """Get the path to the cache file."""
        return Path.home() / ".lcsc2kicad_cache.json"
        
    def load_cache(self):
        """Load cached settings from file."""
        cache_file = self.get_cache_file_path()
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                # Load output directory
                if 'output_directory' in cache_data:
                    self.dir_input.setText(cache_data['output_directory'])
                    self.log_message(f"Loaded cached output directory: {cache_data['output_directory']}")
                    
                # Load symbol library name
                if 'symbol_library' in cache_data:
                    self.symbol_input.setText(cache_data['symbol_library'])
                    
                # Load checkbox states
                if 'include_models' in cache_data:
                    self.models_check.setChecked(cache_data['include_models'])
                    
                if 'download_pdf' in cache_data:
                    self.pdf_check.setChecked(cache_data['download_pdf'])
                    
            except Exception as e:
                self.log_message(f"Error loading cache: {str(e)}")
                
    def save_cache(self):
        """Save current settings to cache file."""
        try:
            cache_data = {
                'output_directory': self.dir_input.text(),
                'symbol_library': self.symbol_input.text(),
                'include_models': self.models_check.isChecked(),
                'download_pdf': self.pdf_check.isChecked()
            }
            
            cache_file = self.get_cache_file_path()
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
                
            self.log_message(f"Settings cached to {cache_file}")
            
        except Exception as e:
            self.log_message(f"Error saving cache: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
