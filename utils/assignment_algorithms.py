import random
from typing import List, Dict, Callable
from time import time
from random import shuffle, seed, choice

def set_seed(seed_value: int | None):
    if seed_value is not None:
        seed(seed_value)
        print(f"Seeding with {seed_value}")

def algorithm_wrapper(func: Callable) -> Callable:
    """Wrapper to ensure family_ids are sorted before running the algorithm"""
    def wrapped(family_ids: List[str], seed_value: int | None = None) -> Dict[str, str]:
        sorted_ids = sorted(family_ids)
        return func(sorted_ids, seed_value)
    return wrapped

@algorithm_wrapper
def random_choice_with_removal_shuffled(family_ids: List[str], seed_value: int = None) -> Dict[str, str]:
    """Current algorithm from assigner.py - Picks random receiver and removes from pool"""
    set_seed(seed_value)
    
    while True:
        receiving_ids = family_ids.copy()
        shuffle(family_ids)
        valid = True
        assignments = {}

        for giver in family_ids:
            possible_receivers = [r for r in receiving_ids if r != giver]
            if not possible_receivers:
                valid = False
                break
            receiver = choice(possible_receivers)
            assignments[giver] = receiver
            receiving_ids.remove(receiver)
        
        if valid:
            return assignments

@algorithm_wrapper
def random_choice_with_removal_no_shuffle(family_ids: List[str], seed_value: int) -> Dict[str, str]:
    """Alternative implementation of random choice with removal
    
    INVALID ALGORITHM:
    - Consider n=3, A>B>C or C>B>A are the only valid derangments
    - A>B 50%; then the only valid option is A>B>C
    - However, we are going to reject this 50% of the time, specifically when B chooses A
    - A>C 50%; then the only valid option is C>B>A
    - This will never be rejected, because B will always choose A
    - A>C>B happens 66.6% of the time
    - C>B>A happens 33.3% of the time

    """
    set_seed(seed_value)
    
    while True:
        receiving_ids = family_ids.copy()
        valid = True
        assignments = {}
        
        for giver in family_ids:
            possible_receivers = [r for r in receiving_ids if r != giver]
            if not possible_receivers:
                valid = False
                break
            receiver = random.choice(possible_receivers)
            assignments[giver] = receiver
            receiving_ids.remove(receiver)
        
        if valid:
            return assignments

@algorithm_wrapper
def smart_last_choice_with_shuffle(family_ids: List[str], seed_value: int) -> Dict[str, str]:
    """
    INVALID ALGORITHM WITHOUT SHUFFLE:
        WORKS FOR n = 3
        FAILS when n = 4 without shuffle

        ABCD
        Problem: C will disproportionately need to choose D to make a valid pairing

    """
    set_seed(seed_value)
    
    receiving_ids = family_ids.copy()
    shuffle(family_ids)
    assignments = {}
    
    for giver in family_ids:
        possible_receivers = [r for r in receiving_ids if r != giver]
        if len(receiving_ids) == 2 and family_ids[-1] in possible_receivers:
            receiver = family_ids[-1]
        else:
            receiver = random.choice(possible_receivers)
        assignments[giver] = receiver
        receiving_ids.remove(receiver)
    
    return assignments

def validate_assignments(family_ids, assignments: Dict[str, str]) -> bool:
    return all(assignments[giver] != giver for giver in assignments) and len(assignments) == len(family_ids)

@algorithm_wrapper
def shuffle_first_valid(family_ids: List[str], seed_value: int) -> Dict[str, str]:
    """Shuffle receivers and take first valid option
    
    INVALID ALGORITHM:
    - A>B 50%; then the only valid option is A>B>C
    - A>C 50%; then the only valid option is C>B>A
    - A now has a higher chance of being first in the shuffled list; it is 1st or 2nd 66.6% of the time
    - So if A→B, 2/3 of the time it will be invalid (if A is 1st or 2nd, then B→A)
    - But if A→C, it's ALWAYS valid, because B will always choose A

    All possible shuffling:
    | [A, B, C] | A → B, B → A, INVALID |
    | [A, C, B] | A → C, B → A, C → B | CBA
    | [B, A, C] | A → B, B → A, INVALID |
    | [B, C, A] | A → B, B → C, C → A | ABC
    | [C, A, B] | A → C, B → A, C → B | CBA
    | [C, B, A] | A → C, B → A, C → B | CBA

    """
    set_seed(seed_value)
    
    while True:
        receiving_ids = family_ids.copy()
        shuffle(receiving_ids)
        valid = True
        assignments = {}
        
        for giver in family_ids:
            possible_receivers = [r for r in receiving_ids if r != giver]
            if not possible_receivers:
                valid = False
                break
            receiver = possible_receivers[0]
            assignments[giver] = receiver
            receiving_ids.remove(receiver)
        
        if valid:
            return assignments

@algorithm_wrapper
def double_shuffle(family_ids: List[str], seed_value: int) -> Dict[str, str]:
    """Shuffle receivers and take first valid option"""
    set_seed(seed_value)
    
    while True:
        receiving_ids = family_ids.copy()
        shuffle(receiving_ids)
        shuffle(family_ids)
        valid = True
        assignments = {}
        
        for giver in family_ids:
            possible_receivers = [r for r in receiving_ids if r != giver]
            if not possible_receivers:
                valid = False
                break
            receiver = possible_receivers[0]
            assignments[giver] = receiver
            receiving_ids.remove(receiver)
        
        if valid:
            return assignments

@algorithm_wrapper
def shuffle_and_zip(family_ids: List[str], seed_value: int) -> Dict[str, str]:
    """Simple shuffle and zip approach - Shuffles once and zips with original"""
    set_seed(seed_value)

    while True:
        receiving_ids = family_ids.copy()
        shuffle(receiving_ids)

        if all(giver != receiver for giver, receiver in zip(family_ids, receiving_ids)):
            return dict(zip(family_ids, receiving_ids))

best_algorithm = random_choice_with_removal_shuffled

def evaluate_algorithm(algo: Callable, family_count: int, total_runs: int = 1000) -> tuple:
    """Evaluate algorithm performance and distribution"""
    family_ids = [str(i) for i in range(family_count)]
    
    # Track timing
    start_time = time()
    
    # Initialize tracking dictionary for all possible pairings
    pairing_counts = {}
    for giver in family_ids:
        pairing_counts[giver] = {receiver: 0 for receiver in family_ids if receiver != giver}
    
    # Run assignments
    for seed_value in range(total_runs):
        assignments = algo(family_ids, seed_value=seed_value)
        assert validate_assignments(family_ids, assignments)

        # Count each pairing
        for giver, receiver in assignments.items():
            pairing_counts[giver][receiver] += 1
    
    elapsed_time = time() - start_time
    
    # Calculate distribution statistics
    expected_percentage = 100.0 / (family_count - 1)
    max_deviation = 0
    
    for giver in family_ids:
        for receiver, count in pairing_counts[giver].items():
            percentage = (count / total_runs) * 100
            deviation = abs(percentage - expected_percentage)
            max_deviation = max(max_deviation, deviation)
    
    return elapsed_time, max_deviation

if __name__ == "__main__":
    algorithms = [
        smart_last_choice_with_shuffle,
        random_choice_with_removal_shuffled,
        double_shuffle,
        shuffle_and_zip,
        random_choice_with_removal_no_shuffle,
        shuffle_first_valid,
    ]
    TRIALS = 100000
    N = 4
    print(f"Evaluating algorithms with {N} participants over {TRIALS} trials:")
    print("-" * 60)
    print(f"{'Algorithm':<30} {'Time (s)':<15} {'Max Deviation %'}")
    print("-" * 60)
    
    for algo in algorithms:
        time_taken, deviation = evaluate_algorithm(algo, family_count=N, total_runs=TRIALS)
        print(f"{algo.__name__:<30} {time_taken:<15.3f} {deviation:.2f}")