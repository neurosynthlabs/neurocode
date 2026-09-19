using System;
using Acme.Billing;

namespace Acme.App
{
    public static class Program
    {
        public static void Main(string[] args)
        {
            Console.WriteLine(new Invoice().Total());
        }
    }
}
