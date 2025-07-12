// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import "@openzeppelin/contracts@5.0.2/utils/introspection/ERC165.sol";
import "@openzeppelin/contracts@5.0.2/utils/introspection/IERC165.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC721/IERC721.sol";
import "@openzeppelin/contracts@5.0.2/access/AccessControl.sol";
import "@openzeppelin/contracts@5.0.2/utils/Pausable.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC20/extensions/IERC20Metadata.sol";
import "@openzeppelin/contracts@5.0.2/token/ERC721/extensions/IERC721Metadata.sol";
import "@openzeppelin/contracts@5.0.2/utils/Strings.sol";

contract UnifiedTokenSystem is ERC165, ERC20, ERC721, AccessControl, Pausable {
    bytes32 public constant ADMIN_ROLE = keccak256("ADMIN_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    // ERC20 Token Details
    string private constant CUAN_NAME = "CUAN Token";
    string private constant CUAN_SYMBOL = "CUAN";
    uint256 private constant CUAN_TOTAL_SUPPLY = 1_000_000 * 10**18;
    string private constant GREY_NAME = "GREY Token";
    string private constant GREY_SYMBOL = "GRY";
    uint256 private constant GREY_TOTAL_SUPPLY = 1_000_000 * 10**18;

    // ERC721 Token Details
    string private constant NFT_NAME = "2048 CUAN";
    string private constant NFT_SYMBOL = "2048C";
    uint256 private constant NFT_MAX_SUPPLY = 100_000;
    uint256 private _nftTokenIdCounter;

    // Allowed tokens for deposit/withdrawal (ERC20 only)
    mapping(address => bool) public allowedTokens;

    // Staking for ERC20
    struct StakeInfo {
        uint256 amount;
        uint256 lastStakedTime;
        uint256 accumulatedReward;
    }
    mapping(address => mapping(address => StakeInfo)) public stakes; // user => token => StakeInfo
    uint256 public rewardRate = 11574; // 1% per day = 0.0000011574% per second
    uint256 public constant TIME_DENOMINATOR = 86400; // 1 day in seconds

    event Deposit(address indexed user, address indexed token, uint256 amount);
    event Withdrawal(address indexed user, address indexed token, uint256 amount);
    event Staked(address indexed user, address indexed token, uint256 amount);
    event Unstaked(address indexed user, address indexed token, uint256 amount, uint256 reward);
    event TokenAdded(address indexed token);
    event TokenRemoved(address indexed token);
    event RewardRateUpdated(uint256 newRate);
    event AirdropBatch(address indexed token, address[] recipients, uint256 amount);
    event Minted(address indexed to, uint256 amount);
    event NFTMinted(address indexed to, uint256 tokenId);

    constructor() 
        ERC20(CUAN_NAME, CUAN_SYMBOL)
        ERC721(NFT_NAME, NFT_SYMBOL)
    {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(ADMIN_ROLE, msg.sender);
        _grantRole(PAUSER_ROLE, msg.sender);

        // Mint initial ERC20 supplies
        _mint(msg.sender, CUAN_TOTAL_SUPPLY / 2); // Half to owner
        _mint(address(this), CUAN_TOTAL_SUPPLY / 2); // Half to contract
        _mint(msg.sender, GREY_TOTAL_SUPPLY / 2); // Half to owner (GREY as second ERC20)
        _mint(address(this), GREY_TOTAL_SUPPLY / 2); // Half to contract

        _nftTokenIdCounter = 1; // Start NFT token ID from 1
    }

    // ERC165 Interface Support
    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC165, ERC721, AccessControl)
        returns (bool)
    {
        return
            interfaceId == type(IERC20).interfaceId ||
            interfaceId == type(IERC721).interfaceId ||
            super.supportsInterface(interfaceId);
    }

    // ERC20 Metadata Overrides
    function name() public view virtual override(ERC20) returns (string memory) {
        return CUAN_NAME; // Default to CUAN, can be extended for GREY if needed
    }

    function symbol() public view virtual override(ERC20) returns (string memory) {
        return CUAN_SYMBOL; // Default to CUAN
    }

    function decimals() public view virtual override(ERC20) returns (uint8) {
        return 18;
    }

    // ERC721 Metadata
    function tokenURI(uint256 tokenId) public view virtual override returns (string memory) {
        require(_exists(tokenId), "ERC721Metadata: URI query for nonexistent token");
        return string(abi.encodePacked("https://example.com/metadata/", Strings.toString(tokenId)));
    }

    // Admin Functions
    function addToken(address token) external onlyRole(ADMIN_ROLE) {
        require(token != address(0), "Invalid token");
        allowedTokens[token] = true;
        emit TokenAdded(token);
    }

    function removeToken(address token) external onlyRole(ADMIN_ROLE) {
        require(allowedTokens[token], "Token not allowed");
        allowedTokens[token] = false;
        emit TokenRemoved(token);
    }

    function setRewardRate(uint256 newRate) external onlyRole(ADMIN_ROLE) {
        require(newRate > 0, "Invalid rate");
        rewardRate = newRate;
        emit RewardRateUpdated(newRate);
    }

    function mint(address to, uint256 amount) external onlyRole(ADMIN_ROLE) {
        require(to != address(0), "Invalid recipient");
        require(amount > 0, "Invalid amount");
        _mint(to, amount);
        emit Minted(to, amount);
    }

    function mintNFT(address to) external onlyRole(ADMIN_ROLE) whenNotPaused {
        require(to != address(0), "Invalid recipient");
        require(_nftTokenIdCounter <= NFT_MAX_SUPPLY, "Max supply reached");
        uint256 tokenId = _nftTokenIdCounter;
        _mint(to, tokenId);
        _nftTokenIdCounter += 1;
        emit NFTMinted(to, tokenId);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    // Deposit and Withdrawal (ERC20)
    function deposit(address token, uint256 amount) external whenNotPaused {
        require(allowedTokens[token], "Token not allowed");
        require(amount > 0, "Invalid amount");
        require(IERC20(token).transferFrom(msg.sender, address(this), amount), "Transfer failed");
        _mint(msg.sender, amount); // Mint wrapped version
        emit Deposit(msg.sender, token, amount);
    }

    function withdraw(address token, uint256 amount) external whenNotPaused {
        require(allowedTokens[token], "Token not allowed");
        require(balanceOf(msg.sender) >= amount, "Insufficient balance");
        _burn(msg.sender, amount);
        require(IERC20(token).transfer(msg.sender, amount), "Transfer failed");
        emit Withdrawal(msg.sender, token, amount);
    }

    // Staking and Unstaking (ERC20)
    function stake(address token, uint256 amount) external whenNotPaused {
        require(amount > 0, "Invalid amount");
        require(balanceOf(msg.sender) >= amount, "Insufficient balance");
        _updateReward(msg.sender, token);
        stakes[msg.sender][token].amount += amount;
        stakes[msg.sender][token].lastStakedTime = block.timestamp;
        _transfer(msg.sender, address(this), amount);
        emit Staked(msg.sender, token, amount);
    }

    function unstake(address token, uint256 amount) external whenNotPaused {
        require(amount > 0, "Invalid amount");
        require(stakes[msg.sender][token].amount >= amount, "Insufficient staked amount");
        _updateReward(msg.sender, token);
        uint256 reward = stakes[msg.sender][token].accumulatedReward;
        stakes[msg.sender][token].amount -= amount;
        stakes[msg.sender][token].accumulatedReward = 0;
        stakes[msg.sender][token].lastStakedTime = block.timestamp;
        _transfer(address(this), msg.sender, amount);
        if (reward > 0) {
            _mint(msg.sender, reward);
        }
        emit Unstaked(msg.sender, token, amount, reward);
    }

    function _updateReward(address user, address token) internal {
        StakeInfo storage userStake = stakes[user][token]; // Ubah 'stake' menjadi 'userStake'
        if (userStake.amount > 0) {
            uint256 timeElapsed = block.timestamp - userStake.lastStakedTime;
            uint256 reward = (userStake.amount * rewardRate * timeElapsed) / (TIME_DENOMINATOR * 1000000);
            userStake.accumulatedReward += reward;
            userStake.lastStakedTime = block.timestamp;
        }
    }

    // Airdrop Batch Function for UnifiedTokenSystem
    function airdropBatch(address token, address[] calldata recipients, uint256 amount) external onlyRole(ADMIN_ROLE) {
        require(recipients.length > 0, "No recipients");
        require(amount > 0, "Invalid amount");
        require(balanceOf(address(this)) >= recipients.length * amount, "Insufficient balance");

        for (uint256 i = 0; i < recipients.length; i++) {
            require(recipients[i] != address(0), "Invalid recipient");
            if (allowedTokens[token]) {
                _transfer(address(this), recipients[i], amount); // ERC20 transfer
            } else if (token == address(this) && _nftTokenIdCounter <= NFT_MAX_SUPPLY) {
                uint256 tokenId = _nftTokenIdCounter;
                _mint(recipients[i], tokenId); // ERC721 mint
                _nftTokenIdCounter += 1;
            }
        }
        emit AirdropBatch(token, recipients, amount);
    }

    // Approve Function (Inherited from ERC20)
    function approve(address spender, uint256 amount) public virtual override returns (bool) {
        return super.approve(spender, amount);
    }
}
