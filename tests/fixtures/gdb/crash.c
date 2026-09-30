int main(void) {
    *(volatile int *)0 = 1;
    return 0;
}
