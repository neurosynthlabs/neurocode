import Foundation

struct Cart {
    var items: [String]

    func count() -> Int {
        return items.count
    }
}

class Store {
    func open() {}
}

protocol Priced {
    func price() -> Int
}

enum Status {
    case open, paid
}

func total(cart: Cart) -> Int {
    if cart.count() > 2 { return 1 }
    return 0
}

private func hidden() {}
