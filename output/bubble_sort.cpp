#include <iostream>
#include <vector>
#include <utility>

// 冒泡排序：对任意可比较类型做升序排列。
// 相邻逆序就交换，每轮把当前最大的顶到末尾；一轮没有交换即已有序。
template <typename T>
void bubble_sort(std::vector<T>& a) {
    for (std::size_t end = a.size(); end > 1; --end) {
        bool swapped = false;
        for (std::size_t i = 0; i + 1 < end; ++i) {
            if (a[i + 1] < a[i]) {
                std::swap(a[i], a[i + 1]);
                swapped = true;
            }
        }
        if (!swapped) break;
    }
}

template <typename T>
void show(const char* label, const std::vector<T>& a) {
    std::cout << label << ": ";
    for (const auto& v : a) std::cout << v << ' ';
    std::cout << '\n';
}

int main() {
    std::vector<int> ints{5, 2, 9, 1, 5, 6};
    std::vector<std::string> words{"pear", "apple", "fig", "banana"};

    bubble_sort(ints);
    bubble_sort(words);

    show("ints", ints);
    show("words", words);

    std::cout << (ints == std::vector<int>{1, 2, 5, 5, 6, 9} ? "sorted ok" : "sorted BAD") << '\n';
    return 0;
}
