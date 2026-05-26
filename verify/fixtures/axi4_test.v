`timescale 1ns/1ps

module axi4_test;
    reg clk;
    reg rst_n;

    // AXI4 Write Address Channel
    reg m_axi_awvalid;
    reg m_axi_awready;
    reg [31:0] m_axi_awaddr;
    reg [7:0] m_axi_awlen;
    reg [2:0] m_axi_awsize;
    reg [1:0] m_axi_awburst;

    // AXI4 Write Data Channel
    reg m_axi_wvalid;
    reg m_axi_wready;
    reg [31:0] m_axi_wdata;
    reg [3:0] m_axi_wstrb;
    reg m_axi_wlast;

    // AXI4 Write Response Channel
    reg m_axi_bvalid;
    reg m_axi_bready;
    reg [1:0] m_axi_bresp;

    // AXI4 Read Address Channel
    reg m_axi_arvalid;
    reg m_axi_arready;
    reg [31:0] m_axi_araddr;
    reg [7:0] m_axi_arlen;

    // AXI4 Read Data Channel
    reg m_axi_rvalid;
    reg m_axi_rready;
    reg [31:0] m_axi_rdata;
    reg [1:0] m_axi_rresp;
    reg m_axi_rlast;

    initial begin
        $dumpfile("axi4_basic.vcd");
        $dumpvars(0, axi4_test);

        // Initialize
        clk = 0;
        rst_n = 0;
        m_axi_awvalid = 0;
        m_axi_awready = 0;
        m_axi_awaddr = 0;
        m_axi_awlen = 0;
        m_axi_awsize = 3'b010; // 4 bytes
        m_axi_awburst = 2'b01; // INCR
        m_axi_wvalid = 0;
        m_axi_wready = 0;
        m_axi_wdata = 0;
        m_axi_wstrb = 4'hF;
        m_axi_wlast = 0;
        m_axi_bvalid = 0;
        m_axi_bready = 0;
        m_axi_bresp = 2'b00; // OKAY
        m_axi_arvalid = 0;
        m_axi_arready = 0;
        m_axi_araddr = 0;
        m_axi_arlen = 0;
        m_axi_rvalid = 0;
        m_axi_rready = 0;
        m_axi_rdata = 0;
        m_axi_rresp = 2'b00;
        m_axi_rlast = 0;

        // Reset
        #20 rst_n = 1;

        // AXI Write Transaction 1: Single beat write to 0x1000
        #20;
        m_axi_awvalid = 1;
        m_axi_awaddr = 32'h1000;
        m_axi_awlen = 8'h00; // 1 beat

        #10 m_axi_awready = 1;
        #10 m_axi_awvalid = 0;
            m_axi_awready = 0;

        // Write data
        m_axi_wvalid = 1;
        m_axi_wdata = 32'hDEADBEEF;
        m_axi_wlast = 1;

        #10 m_axi_wready = 1;
        #10 m_axi_wvalid = 0;
            m_axi_wready = 0;
            m_axi_wlast = 0;

        // Write response
        m_axi_bvalid = 1;
        m_axi_bresp = 2'b00; // OKAY
        m_axi_bready = 1;

        #10 m_axi_bvalid = 0;
            m_axi_bready = 0;

        // AXI Write Transaction 2: Burst write (2 beats) to 0x2000
        #20;
        m_axi_awvalid = 1;
        m_axi_awaddr = 32'h2000;
        m_axi_awlen = 8'h01; // 2 beats

        #10 m_axi_awready = 1;
        #10 m_axi_awvalid = 0;
            m_axi_awready = 0;

        // Write data beat 1
        m_axi_wvalid = 1;
        m_axi_wdata = 32'hCAFEBABE;
        m_axi_wlast = 0;

        #10 m_axi_wready = 1;
        #10 m_axi_wdata = 32'h12345678;
            m_axi_wlast = 1;

        #10 m_axi_wvalid = 0;
            m_axi_wready = 0;
            m_axi_wlast = 0;

        // Write response
        m_axi_bvalid = 1;
        m_axi_bresp = 2'b00; // OKAY
        m_axi_bready = 1;

        #10 m_axi_bvalid = 0;
            m_axi_bready = 0;

        // AXI Read Transaction: Single beat read from 0x1000
        #20;
        m_axi_arvalid = 1;
        m_axi_araddr = 32'h1000;
        m_axi_arlen = 8'h00; // 1 beat

        #10 m_axi_arready = 1;
        #10 m_axi_arvalid = 0;
            m_axi_arready = 0;

        // Read data
        m_axi_rvalid = 1;
        m_axi_rdata = 32'hDEADBEEF;
        m_axi_rresp = 2'b00; // OKAY
        m_axi_rlast = 1;
        m_axi_rready = 1;

        #10 m_axi_rvalid = 0;
            m_axi_rready = 0;
            m_axi_rlast = 0;

        // Protocol Violation: WVALID before AWVALID
        #20;
        m_axi_wvalid = 1;
        m_axi_wdata = 32'hBADBAD00;

        #20 m_axi_wvalid = 0;

        #20;
        m_axi_awvalid = 1;
        m_axi_awaddr = 32'h3000;
        m_axi_awlen = 8'h00;

        #10 m_axi_awready = 1;
        #10 m_axi_awvalid = 0;
            m_axi_awready = 0;

        #50 $finish;
    end

    always #5 clk = ~clk;

endmodule
