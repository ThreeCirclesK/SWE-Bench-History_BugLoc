from swebench.harness.run_evaluation import *
from git import Repo
import os
from utils import *

# Use your GitHub token (create one at https://github.com/settings/tokens)
auth_token = os.getenv("GITHUB_TOK")
#auth_token =

# Load full dataset 
dataset_name = 'princeton-nlp/SWE-bench_Verified'
split = "test"
full_dataset = load_swebench_dataset(dataset_name, split)
print(len(full_dataset))

# Path where SWEBEnch Repositories are cloned
Repodir = "Repodir"#"/media/zero/ssd2/SWEBenchRepo"
repos = list(set([data['repo'] for data in full_dataset]))
print("Target Repos: ", repos)



for reponame in list(repo_cutoff.keys()):
    save_mapping_brs(Repodir, reponame, auth_token)