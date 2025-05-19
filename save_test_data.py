import pickle
from swebench.harness.run_evaluation import *
import os 
import subprocess
import json
import argparse

# Load full dataset 
dataset_name = 'princeton-nlp/SWE-bench_Verified'
split = "test"
full_dataset = load_swebench_dataset(dataset_name, split)
print(len(full_dataset))

with open("nid2issue.pkl","rb") as f:
    nid2issue = pickle.load(f)
    
def save_test_index(repodir, reponame):
    repo_path = f"{repodir}/{reponame}"
    trg_test_data = [x for x in full_dataset if x['repo']==reponame.replace("+","/")]
    bugs = []
    for data in trg_test_data:
        item = {}
        diff = data['patch'].split('\n')
        diff = [x for x in diff if x.startswith("diff")]
        fixed_files = [x.split(" ")[-1][2:] for x in diff]
        item['fixed'] = fixed_files
        item['parent_commit'] = data['base_commit']
        item['brtext'] = data['problem_statement']
        item['instance_id'] = data['instance_id']
        item['number'] = int(data['instance_id'].split("-")[-1])
        bugs.append(item)

    if not os.path.isdir("test_bugs"):
        os.makedirs("test_bugs")
    with open(f"test_bugs/{reponame}_bugs.pkl",'wb') as f:
        pickle.dump(bugs, f)

    # Directory to store all files and index maps
    indexing_dir = os.path.abspath(f"{repo_path}_test_indexing")
    os.makedirs(indexing_dir, exist_ok=True)

    first_bug = True
    fidx = 0

    commit_index_map = {}       # path → f"{fidx}.py"
    path_to_content = {}        # path → last content bytes
    prev_parent_sha = None      # for comparing across bugs

    for bug in bugs:
        commit_sha = bug['parent_commit']
        brid = bug['number']
        print(f"\nProcessing commit: {commit_sha}")

        original_cwd = os.getcwd()
        os.chdir(repo_path)

        saved_index_map = {}
        full_index_map = {}
        current_index_map = {}

        try:
            # === STEP 1: Get parent commit SHA of current bug
            curr_parent_sha = subprocess.check_output(
                ["git", "rev-parse", f"{commit_sha}"], ## TEST DATA: BASE COMMIT IS PARENT COMMIT
                text=True
            ).strip()
            print(f"Parent of current commit: {curr_parent_sha}")

            # === STEP 2: Get all .py files in current parent commit
            all_py_files = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", curr_parent_sha],
                text=True
            ).splitlines()
            all_py_files = [f for f in all_py_files if f.endswith(".py")]
            print(f"Found {len(all_py_files)} Python files in current parent commit.")

            # === STEP 3: Copy previous map or initialize for first bug
            if first_bug:
                current_index_map = {}
            else:
                current_index_map = commit_index_map.copy()

            # === STEP 4: Remove deleted files
            if not first_bug:
                for path in list(current_index_map.keys()):
                    if path not in all_py_files:
                        del current_index_map[path]
                        path_to_content.pop(path, None)

            # === STEP 5: Determine changed files between prev and current parent
            if first_bug:
                target_files = all_py_files
                print("[First bug] Saving all files.")
            else:
                # Compare: prev_parent_sha vs curr_parent_sha
                changed_files = subprocess.check_output(
                    ["git", "diff", "--name-only", "--diff-filter=AM", prev_parent_sha, curr_parent_sha],
                    text=True
                ).splitlines()
                target_files = [f for f in changed_files if f.endswith(".py")]
                print(f"Changed .py files between {prev_parent_sha} and {curr_parent_sha}: {len(target_files)}")

            # === STEP 6: Save updated files if content changed
            for file_path in target_files:
                try:
                    raw_content = subprocess.check_output(
                        ["git", "show", f"{curr_parent_sha}:{file_path}"]
                    )

                    should_save = (
                        first_bug or
                        file_path not in path_to_content or
                        path_to_content[file_path] != raw_content
                    )

                    if should_save:
                        try:
                            file_content = raw_content.decode("utf-8")
                        except UnicodeDecodeError:
                            print(f"Warning: {file_path} not UTF-8. Replacing errors.")
                            file_content = raw_content.decode("utf-8", errors="replace")

                        save_name = f"{fidx}.py"
                        dest_path = os.path.join(indexing_dir, save_name)
                        with open(dest_path, "w", encoding="utf-8") as f:
                            f.write(file_content)

                        path_to_content[file_path] = raw_content
                        current_index_map[file_path] = save_name
                        saved_index_map[save_name] = file_path
                        fidx += 1

                except subprocess.CalledProcessError:
                    print(f"Failed to extract: {file_path}")

            # === STEP 7: Build full_index and path_to_file (correct indexing)
            temp_index = []
            for path in all_py_files:
                if path in current_index_map:
                    idx = int(current_index_map[path].replace('.py', ''))
                    temp_index.append([idx, path])
            temp_index = sorted(temp_index, key=lambda x: x[0])

            full_index_map = {}
            for idx, path in temp_index:
                full_index_map[f"{idx}.py"] = path

            path_to_file = {
                path: current_index_map[path]
                for path in all_py_files
                if path in current_index_map
            }

            # === STEP 8: Save JSON index file
            index_filename = f"{brid}_{commit_sha}.json"
            index_path = os.path.join(indexing_dir, index_filename)
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump({
                    "full_index": full_index_map,
                    "saved_files": saved_index_map,
                    "path_to_file": path_to_file
                }, f, indent=2)

            print(f"Index written: {index_filename}")

            # === STEP 9: Update global state
            commit_index_map = current_index_map
            first_bug = False
            prev_parent_sha = curr_parent_sha

        finally:
            os.chdir(original_cwd)


def main():
    parser = argparse.ArgumentParser(description="Github repo processor")
    parser.add_argument(
        "--repo_name", 
        required=True, 
        help="Repository in 'owner+repo' format (e.g., 'sphinx-doc+sphinx')"
    )
    parser.add_argument(
        "--repodir",
        default="Repodir",
        help="Local directory name for the repo (default: 'Repodir')"
    )
    args = parser.parse_args()

    #Repodir = "Repodir"
    #repo_name = "sphinx-doc+sphinx"
    print(f"Repodir: {args.repodir}")
    save_test_index(args.repodir, args.repo_name)

if __name__ == "__main__":
    main()