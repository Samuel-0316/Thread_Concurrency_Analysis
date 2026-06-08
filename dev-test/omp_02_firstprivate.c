#include <stdio.h>
#include <omp.h>

#define N 100

int main() {
    int i;
    int temp_val = 10; // Issue: RAW + WAW race, needs firstprivate
    int result[N];

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        // Read before write: needs the original value of temp_val (10)
        result[i] = temp_val * i;
        
        // Write: overwritten by each thread
        temp_val = i;
    }

    printf("Done. result[1] = %d\n", result[1]);
    return 0;
}