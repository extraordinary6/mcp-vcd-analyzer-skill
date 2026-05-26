`timescale 1ns/1ps

// APB3 protocol test
module apb_test;
    reg clk;
    reg rst_n;

    // APB signals
    reg [31:0] paddr;
    reg pwrite;
    reg psel;
    reg penable;
    reg [31:0] pwdata;
    reg pready;
    reg [31:0] prdata;
    reg pslverr;

    initial begin
        $dumpfile("apb_basic.vcd");
        $dumpvars(0, apb_test);

        clk = 0;
        rst_n = 0;
        paddr = 0;
        pwrite = 0;
        psel = 0;
        penable = 0;
        pwdata = 0;
        pready = 0;
        prdata = 0;
        pslverr = 0;

        #20 rst_n = 1;

        // APB Write Transaction 1: Write 0xDEADBEEF to 0x1000
        // SETUP phase
        #20 paddr = 32'h1000;
            pwrite = 1;
            pwdata = 32'hDEADBEEF;
            psel = 1;
        // ACCESS phase
        #10 penable = 1;
        // Slave ready
        #10 pready = 1;
        // Complete
        #10 psel = 0;
            penable = 0;
            pready = 0;

        // APB Read Transaction: Read from 0x1000
        #20 paddr = 32'h1000;
            pwrite = 0;
            psel = 1;
        #10 penable = 1;
        #10 pready = 1;
            prdata = 32'hDEADBEEF;
        #10 psel = 0;
            penable = 0;
            pready = 0;

        // APB Write with error
        #20 paddr = 32'h2000;
            pwrite = 1;
            pwdata = 32'hCAFEBABE;
            psel = 1;
        #10 penable = 1;
        #20 pready = 1;
            pslverr = 1;  // Slave error
        #10 psel = 0;
            penable = 0;
            pready = 0;
            pslverr = 0;

        #50 $finish;
    end

    always #5 clk = ~clk;

endmodule
