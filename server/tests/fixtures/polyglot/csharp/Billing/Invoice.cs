namespace Acme.Billing;

public class Invoice
{
    public decimal Amount { get; set; }

    public decimal Total()
    {
        if (Amount > 100 && Amount < 1000)
        {
            return Amount * 2;
        }
        return Amount;
    }

    private void Reset() { Amount = 0; }
}

public interface IPriced
{
    decimal Price();
}

public enum Status { Open, Paid }

public struct Line { public int Qty; }

public record Receipt(string Id);
