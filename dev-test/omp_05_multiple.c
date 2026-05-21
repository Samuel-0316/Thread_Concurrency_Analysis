#include <stdio.h>
#include <omp.h>

#define N 200

int main() {
    int i;
    int sum = 0;           // Needs reduction
    int multiplier = 5;    // Needs firstprivate
    int final_calc = 0;    // Needs lastprivate
    int result[N];

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        // Read before write
        int val = multiplier * i;
        result[i] = val;
        
        // Write to multiplier
        multiplier = i % 10;
        
        // Accumulation
        sum += val;
        
        // Final value
        final_calc = val;
    }

    printf("Sum: %d\n", sum);
    printf("Final Calculation: %d\n", final_calc);
    return 0;
}
