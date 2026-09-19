mod billing;

use crate::billing::Invoice;
use std::fmt;

fn main() {
    let invoice = Invoice { amount: 3 };
    println!("{}", invoice.total());
}
