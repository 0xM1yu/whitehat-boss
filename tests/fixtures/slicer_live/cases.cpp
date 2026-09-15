extern void consume_cpp(const int *data, int count);

void cpp_entry(int count)
{
    int payload[]{
        11, 22, 33
    };
    consume_cpp(payload, count);
}

int unrelated_cpp(int value)
{
    return value + 777;
}
