#include <stdio.h>
#include <omp.h>

#define N 1000

int main() {
    int i;
    long long total_sum = 0; // Issue: WAW race

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        // Multiple threads updating total_sum simultaneously
        total_sum += i;
    }

    printf("Total sum: %lld\n", total_sum);
    return 0;
}
