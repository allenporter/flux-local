import tempfile
import pathlib
import json
import os

class TemporaryJsonConfig:
    """A context manager for creating and managing a temporary JSON configuration file."""

    def __init__(self):
        self._temp_file = None

    def __enter__(self):
        """Create a temporary JSON file with initial content '{}'."""
        # Create a named temporary file that can be written to and then read.
        # delete=False is used so that the file is not deleted immediately upon close,
        # allowing its path to be returned and used by other parts of the code.
        # The file will be explicitly deleted in __exit__.
        self._temp_file = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", delete=False
        )
        
        # Write the initial JSON content
        self._temp_file.write("{}")
        
        # Flush the content to ensure it's written to disk
        self._temp_file.flush()
        
        # Store and return the path to this file as a pathlib.Path object
        self.temp_file_path = pathlib.Path(self._temp_file.name)
        return self.temp_file_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure the temporary file is deleted."""
        # Close the file if it's open
        if self._temp_file:
            self._temp_file.close()
        
        # Delete the temporary file
        # Check if the path exists before attempting to delete,
        # though NamedTemporaryFile with delete=False should ensure it's there
        # until we explicitly delete it.
        if hasattr(self, 'temp_file_path') and self.temp_file_path.exists():
            os.unlink(self.temp_file_path)
        
        # Return False to propagate any exceptions that occurred within the 'with' block
        return False

if __name__ == '__main__':
    # Example usage:
    with TemporaryJsonConfig() as tmp_config_path:
        print(f"Temporary config at: {tmp_config_path}")
        print(f"File exists: {tmp_config_path.exists()}")
        with open(tmp_config_path, 'r') as f:
            content = f.read()
            print(f"Content: {content}")
    
    print(f"File exists after exiting context: {tmp_config_path.exists()}")

    # Example with an error
    try:
        with TemporaryJsonConfig() as tmp_config_path_err:
            print(f"Temporary config (error test) at: {tmp_config_path_err}")
            print(f"File exists (error test): {tmp_config_path_err.exists()}")
            raise ValueError("Simulated error")
    except ValueError as e:
        print(f"Caught expected error: {e}")
    
    print(f"File exists after error: {tmp_config_path_err.exists()}")
