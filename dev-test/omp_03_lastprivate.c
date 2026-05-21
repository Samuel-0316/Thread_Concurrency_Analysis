#include <stdio.h>
#include <omp.h>

#define N 50

int main() {
    int i;
    int final_index = 0; // Issue: WAW race, needs lastprivate

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        // All threads overwrite this. We need the value from the logically last iteration (i == N-1).
        final_index = i;
    }

    // Used after the loop!
    printf("The final index processed was: %d\n", final_index);
    return 0;
}
