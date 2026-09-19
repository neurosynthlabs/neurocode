class Cart {
  final List<String> items = [];

  int count() {
    return items.length;
  }
}

enum Status { open, paid }

int total(Cart cart) => cart.count();

void _hidden() {}
