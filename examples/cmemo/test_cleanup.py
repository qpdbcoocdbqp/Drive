import sys
import os
from pathlib import Path

# Add the project root to sys.path to import the module
project_root = r"examples\cmemo"
sys.path.append(project_root)

from module.memory_manager import MemoryManager

def test_cleanup():
    # Initialize MemoryManager pointing to the .cmemo directory
    mm = MemoryManager(base_path=os.path.join(project_root, ".cmemo"))
    
    print("Initial files in memory directory:")
    memory_path = Path(project_root) / ".cmemo" / "memory"
    for f in memory_path.iterdir():
        print(f" - {f.name}")
        
    print("\nRunning cleanup...")
    deleted = mm.cleanup_memories()
    
    if deleted:
        print(f"Deleted files: {', '.join(deleted)}")
    else:
        print("No files were deleted.")
        
    print("\nFiles in memory directory after cleanup:")
    for f in memory_path.iterdir():
        print(f" - {f.name}")

if __name__ == "__main__":
    test_cleanup()
